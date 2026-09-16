"""Daily ingest of AMFI NAVAll.txt: fetch, archive raw bytes forever, parse, upsert.

This is the cheapest high-value thing in the system (§8.2 of the spec): archiving this file
daily is the only way point-in-time category/taxonomy history and universe membership will
ever be reconstructable.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime, timezone

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_identity import amc_id_for, slug as _slug
from indian_mf_mcp.ingest.amc_scheme_registry import auto_register_from_navall
from indian_mf_mcp.normalize.taxonomy import parse_plan_option, parse_plan_option_columns
from indian_mf_mcp.parsers.delimited import NavRow, parse_amfi_date, parse_navall
from indian_mf_mcp.store import repository as repo

# amc_id_for / _slug now live in amc_identity, which is also what maps these ids back to
# adapter keys. Re-exported here because this module was their original home.
__all__ = ["amc_id_for", "fetch_navall", "archive_raw", "ingest_rows", "run_daily_ingest",
           "scheme_id_for"]


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

        # Prefer AMFI's dedicated Plan/Option columns; fall back to the name-embedded tokens
        # (legacy layout, and rows where AMFI left the new columns blank).
        from_columns = parse_plan_option_columns(row.plan_raw, row.option_raw)
        if from_columns is not None:
            info = from_columns
            base_scheme_name = row.scheme_name
        else:
            info = parse_plan_option(row.scheme_name)
            base_scheme_name = info.base_scheme_name
        scheme_id = scheme_id_for(row.amc_name, base_scheme_name)
        if scheme_id not in seen_schemes:
            repo.upsert_scheme(
                conn, scheme_id, amc_id, base_scheme_name,
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
            nav_date = parse_amfi_date(row.date)
            if nav_date:
                nav_batch.append((plan_id, nav_date, row.nav))

    if nav_batch:
        repo.insert_nav_points(conn, nav_batch)
    repo.mark_inactive_plans_not_seen_since(conn, as_of_str)

    warnings: list[str] = []
    if n_plans and not nav_batch:
        # Every row failing NAV/date extraction means AMFI changed the file layout again.
        # Never let that pass as a silent success: the ingest "worked" but stored no NAVs.
        warnings.append(
            f"parsed {n_plans} plan rows but extracted 0 NAV points — "
            "AMFI's NAVAll.txt layout has likely changed; check parsers/delimited.py"
        )

    # Register the scheme -> adapter-hint rows that 'mf-mcp backfill --amc <amc>' drives
    # off. This was written but never called from any ingest path, so scheme_adapter_hint
    # stayed empty on every install and the bulk backfill always exited "no schemes
    # registered". It belongs here: the hint defaults to the scheme's AMFI name, which is
    # exactly what this function has just written.
    hints = auto_register_from_navall(conn)

    return {"amcs": len(seen_amcs), "schemes": len(seen_schemes), "plans": n_plans,
            "nav_points": len(nav_batch), "adapter_hints_registered": hints,
            "warnings": warnings}


def run_daily_ingest(conn: sqlite3.Connection, raw: bytes | None = None, as_of: date | None = None) -> dict:
    as_of = as_of or datetime.now(timezone.utc).date()
    if raw is None:
        raw = fetch_navall()
    digest, path = archive_raw(raw, as_of)
    rows = parse_navall(raw.decode("utf-8", errors="replace"))
    stats = ingest_rows(conn, rows, as_of)
    stats.update({"sha256": digest, "archive_path": path, "as_of": as_of.isoformat(), "rows_parsed": len(rows)})
    return stats
