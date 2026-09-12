"""Daily ingest of AMFI NAVAll.txt: fetch, archive raw bytes forever, parse, upsert.

This is the cheapest high-value thing in the system (§8.2 of the spec): archiving this file
daily is the only way point-in-time category/taxonomy history and universe membership will
ever be reconstructable.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date, datetime, timezone

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.normalize.taxonomy import parse_plan_option
from indian_mf_mcp.parsers.delimited import NavRow, parse_navall
from indian_mf_mcp.store import repository as repo

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _NON_ALNUM_RE.sub("-", text.lower()).strip("-")


def amc_id_for(amc_name: str) -> str:
    return f"amc-{_slug(amc_name)}"


def scheme_id_for(amc_name: str, base_scheme_name: str) -> str:
    key = f"{_slug(amc_name)}::{_slug(base_scheme_name)}"
    return "scheme-" + hashlib.sha1(key.encode()).hexdigest()[:16]


def fetch_navall(client: httpx.Client | None = None) -> bytes:
    headers = {"User-Agent": config.USER_AGENT}
    if client is None:
        with httpx.Client(headers=headers, timeout=60) as c:
            resp = c.get(config.AMFI_NAVALL_URL)
    else:
        resp = client.get(config.AMFI_NAVALL_URL, headers=headers)
    resp.raise_for_status()
    return resp.content


def archive_raw(raw: bytes, as_of: date) -> tuple[str, str]:
    """Write the raw file to the never-evicted archive dir, named by date and content hash."""
    config.ensure_dirs()
    digest = hashlib.sha256(raw).hexdigest()
    path = config.NAVALL_ARCHIVE_DIR / f"{as_of.isoformat()}_{digest[:12]}.txt"
    if not path.exists():
        path.write_bytes(raw)
    return digest, str(path)


def ingest_rows(conn: sqlite3.Connection, rows: list[NavRow], as_of: date) -> dict:
    as_of_str = as_of.isoformat()
    seen_amcs: set[str] = set()
    seen_schemes: set[str] = set()
    nav_batch: list[tuple[str, str, float]] = []
    n_plans = 0

    for row in rows:
        if not row.amc_name:
            continue
        amc_id = amc_id_for(row.amc_name)
        if amc_id not in seen_amcs:
            repo.upsert_amc(conn, amc_id, row.amc_name)
            seen_amcs.add(amc_id)

        info = parse_plan_option(row.scheme_name)
        scheme_id = scheme_id_for(row.amc_name, info.base_scheme_name)
        if scheme_id not in seen_schemes:
            repo.upsert_scheme(
                conn, scheme_id, amc_id, info.base_scheme_name,
                row.scheme_type, row.category, row.sub_category, as_of_str,
            )
            repo.insert_taxonomy_history(
                conn, scheme_id, as_of_str, row.scheme_type, row.category,
                row.sub_category, row.raw_header_string,
            )
            seen_schemes.add(scheme_id)

        plan_id = f"plan-{row.scheme_code}"
        isin = row.isin_div_payout_or_growth or row.isin_div_reinvestment
        repo.upsert_plan(
            conn, plan_id, scheme_id, row.scheme_code, isin,
            info.plan_type, info.option_type, info.idcw_variant, as_of_str,
        )
        n_plans += 1

        if row.nav is not None and row.date:
            nav_date = _parse_amfi_date(row.date)
            if nav_date:
                nav_batch.append((plan_id, nav_date, row.nav))

    if nav_batch:
        repo.insert_nav_points(conn, nav_batch)
    repo.mark_inactive_plans_not_seen_since(conn, as_of_str)

    return {"amcs": len(seen_amcs), "schemes": len(seen_schemes), "plans": n_plans,
            "nav_points": len(nav_batch)}


def _parse_amfi_date(date_str: str) -> str | None:
    """AMFI dates look like '12-Sep-2026'."""
    for fmt in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def run_daily_ingest(conn: sqlite3.Connection, raw: bytes | None = None, as_of: date | None = None) -> dict:
    as_of = as_of or datetime.now(timezone.utc).date()
    if raw is None:
        raw = fetch_navall()
    digest, path = archive_raw(raw, as_of)
    rows = parse_navall(raw.decode("utf-8", errors="replace"))
    stats = ingest_rows(conn, rows, as_of)
    stats.update({"sha256": digest, "archive_path": path, "as_of": as_of.isoformat(), "rows_parsed": len(rows)})
    return stats
