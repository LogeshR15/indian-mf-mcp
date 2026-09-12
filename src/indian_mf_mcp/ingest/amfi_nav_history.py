"""Backfill historical NAV from AMFI's DownloadNAVHistoryReport_Po.aspx.

NAVAll.txt is a one-day snapshot, so on its own the store can only ever hold a single NAV
point per plan — which makes every return/risk metric uncomputable. This module is what gives
`get_fund_performance` something to compute over.

Verified live 2026-09-13:
  - `tp` selects the scheme universe and all three are needed for full coverage:
    1 = open-ended (~8,560 scheme codes), 2 = close-ended (~180), 3 = interval (~4).
  - `frmdt`/`todt` take AMFI's own `%d-%b-%Y` form. A 3-month range returns ~74 MB and takes
    ~60s, so requests are chunked by calendar month to bound memory and make a long backfill
    resumable at month granularity.

Unlike NAVAll.txt the raw payloads are NOT archived: a decade of history is several GB, and
unlike the daily snapshot (whose point-in-time taxonomy is destroyed if not captured that
day) it stays re-derivable from AMFI on demand.

This ingest deliberately only writes `nav_point`. The scheme/plan universe and its
point-in-time taxonomy come from the daily NAVAll ingest; scheme codes not already known
there are counted and skipped rather than conjuring half-populated scheme rows from a
historical file.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.parsers.delimited import AMFI_DATE_FMT, parse_amfi_date, parse_navall
from indian_mf_mcp.store import repository as repo

# tp values -> the scheme universe each one covers.
SCHEME_UNIVERSES = {"1": "open-ended", "2": "close-ended", "3": "interval"}

_ONE_DAY = timedelta(days=1)


def _amfi_date(value: date) -> str:
    return value.strftime(AMFI_DATE_FMT)


def month_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Split [start, end] into calendar-month (from, to) pairs, clipped to the bounds."""
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        yield cursor, min(next_month - _ONE_DAY, end)
        cursor = next_month


def fetch_nav_history(frm: date, to: date, tp: str = "1",
                      client: httpx.Client | None = None) -> str:
    params = {"tp": tp, "frmdt": _amfi_date(frm), "todt": _amfi_date(to)}
    headers = {"User-Agent": config.USER_AGENT}
    if client is None:
        with httpx.Client(headers=headers, timeout=300, follow_redirects=True) as c:
            resp = c.get(config.AMFI_NAV_HISTORY_URL, params=params)
    else:
        resp = client.get(config.AMFI_NAV_HISTORY_URL, params=params,
                          headers=headers, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def ingest_history_text(conn: sqlite3.Connection, text: str) -> dict:
    """Parse one history payload and upsert its NAV points for already-known plans."""
    known_plans = {row[0] for row in conn.execute("SELECT plan_id FROM plan")}
    nav_batch: list[tuple[str, str, float]] = []
    unknown_codes: set[str] = set()
    unparseable_dates = 0
    rows = parse_navall(text)

    for row in rows:
        if row.nav is None or not row.date:
            continue
        nav_date = parse_amfi_date(row.date)
        if not nav_date:
            unparseable_dates += 1
            continue
        plan_id = f"plan-{row.scheme_code}"
        if plan_id not in known_plans:
            unknown_codes.add(row.scheme_code)
            continue
        nav_batch.append((plan_id, nav_date, row.nav))

    if nav_batch:
        repo.insert_nav_points(conn, nav_batch)

    return {
        "rows_parsed": len(rows),
        "nav_points": len(nav_batch),
        "unknown_scheme_codes": len(unknown_codes),
        "unparseable_dates": unparseable_dates,
    }


def _chunk_run_id(frm: date, to: date) -> str:
    return f"navhist-{frm.isoformat()}_{to.isoformat()}"


def _completed_chunks(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in conn.execute(
            "SELECT run_id FROM ingest_run WHERE source = ? AND status = 'ok'",
            ("amfi_nav_history",),
        )
    }


def _record_chunk(conn: sqlite3.Connection, run_id: str, started_at: str,
                  status: str, detail: dict) -> None:
    conn.execute(
        """INSERT INTO ingest_run (run_id, source, started_at, finished_at, status, detail_json)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(run_id) DO UPDATE SET finished_at=excluded.finished_at,
               status=excluded.status, detail_json=excluded.detail_json""",
        (run_id, "amfi_nav_history", started_at,
         datetime.now(timezone.utc).isoformat(), status, json.dumps(detail)),
    )


def backfill_nav_history(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    universes: tuple[str, ...] = ("1", "2", "3"),
    force: bool = False,
    progress=None,
) -> dict:
    """Backfill NAV history month by month over [start, end].

    Completed months are recorded in `ingest_run` and skipped on a rerun unless `force`, so an
    interrupted multi-year backfill resumes where it stopped instead of re-downloading
    gigabytes. Writes are idempotent regardless (nav_point upserts on (plan_id, date)).
    """
    done = set() if force else _completed_chunks(conn)
    totals = {"chunks": 0, "chunks_skipped": 0, "nav_points": 0, "rows_parsed": 0,
              "unknown_scheme_codes": 0, "unparseable_dates": 0}
    warnings: list[str] = []

    for frm, to in month_chunks(start, end):
        run_id = _chunk_run_id(frm, to)
        if run_id in done:
            totals["chunks_skipped"] += 1
            if progress:
                progress(f"{frm} .. {to}: already ingested, skipping")
            continue

        started_at = datetime.now(timezone.utc).isoformat()
        chunk = {"nav_points": 0, "rows_parsed": 0, "unknown_scheme_codes": 0,
                 "unparseable_dates": 0}
        try:
            for tp in universes:
                stats = ingest_history_text(conn, fetch_nav_history(frm, to, tp=tp))
                for key in chunk:
                    chunk[key] += stats[key]
        except (httpx.HTTPError, OSError) as exc:
            # One bad month must not discard the months already committed: record it and
            # continue, so the caller gets a complete picture of what is and isn't loaded.
            _record_chunk(conn, run_id, started_at, "failed", {"error": str(exc)})
            warnings.append(f"{frm} .. {to}: fetch failed ({exc.__class__.__name__}: {exc})")
            if progress:
                progress(f"{frm} .. {to}: FAILED — {exc}")
            continue

        status = "ok" if chunk["nav_points"] else "empty"
        _record_chunk(conn, run_id, started_at, status, chunk)
        totals["chunks"] += 1
        for key in chunk:
            totals[key] += chunk[key]
        if progress:
            progress(f"{frm} .. {to}: +{chunk['nav_points']:,} NAV points")

    if totals["chunks"] and not totals["nav_points"]:
        warnings.append(
            f"fetched {totals['chunks']} month(s) but stored 0 NAV points — "
            "AMFI's history layout may have changed, or no plans are known to the store yet "
            "(run `mf-mcp ingest-navall` first)"
        )

    totals["warnings"] = warnings
    return totals
