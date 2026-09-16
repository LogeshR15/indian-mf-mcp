"""What is actually loaded in this store, and what to run next.

There was no way to answer "is my install working?" short of starting the MCP server and
asking a model a question — and an empty store answers most questions with a plausible
"unavailable" rather than "you haven't ingested anything". This module is the read side
of `mf-mcp status`, and the same counts drive `mf-mcp setup`'s decision about which
ingest steps are already done (so a re-run resumes rather than redoing).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

from indian_mf_mcp import config
from indian_mf_mcp.ingest import amc_identity

# AMFI publishes NAVAll.txt every business day. Anything older than this and the
# scheme universe is stale enough that newly-launched schemes will be missing.
_NAVALL_STALE_DAYS = 4


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else 0


def _value(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def collect(conn: sqlite3.Connection) -> dict:
    """Counts and coverage windows for every ingested data class."""
    nav_start = _value(conn, "SELECT MIN(date) FROM nav_point")
    nav_end = _value(conn, "SELECT MAX(date) FROM nav_point")

    portfolio_amcs = conn.execute(
        """SELECT a.name AS amc_name, s.amc_id,
                  COUNT(DISTINCT ps.scheme_id) AS schemes,
                  MAX(ps.as_of_date) AS latest
           FROM portfolio_snapshot ps
           JOIN scheme s ON s.scheme_id = ps.scheme_id
           JOIN amc a ON a.amc_id = s.amc_id
           GROUP BY s.amc_id
           ORDER BY a.name"""
    ).fetchall()

    caplist_date = _value(conn, "SELECT MAX(effective_date) FROM isin_market_cap")

    return {
        "data_home": str(config.DATA_HOME),
        "db_path": str(config.DB_PATH),
        "db_size_bytes": config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0,
        "amcs": _scalar(conn, "SELECT COUNT(*) FROM amc"),
        "schemes": _scalar(conn, "SELECT COUNT(*) FROM scheme"),
        "schemes_active": _scalar(conn, "SELECT COUNT(*) FROM scheme WHERE active = 1"),
        "plans": _scalar(conn, "SELECT COUNT(*) FROM plan"),
        "nav_points": _scalar(conn, "SELECT COUNT(*) FROM nav_point"),
        "nav_start": nav_start,
        "nav_end": nav_end,
        "nav_months_ingested": _scalar(
            conn, "SELECT COUNT(*) FROM ingest_run WHERE source LIKE 'nav_history%' AND status = 'ok'"
        ),
        "portfolio_snapshots": _scalar(conn, "SELECT COUNT(*) FROM portfolio_snapshot"),
        "portfolio_schemes": _scalar(
            conn, "SELECT COUNT(DISTINCT scheme_id) FROM portfolio_snapshot"
        ),
        "portfolio_by_amc": [dict(r) for r in portfolio_amcs],
        "holdings": _scalar(conn, "SELECT COUNT(*) FROM holding"),
        "documents": _scalar(conn, "SELECT COUNT(*) FROM document"),
        "manager_assignments": _scalar(conn, "SELECT COUNT(*) FROM manager_assignment"),
        "ter_rows": _scalar(conn, "SELECT COUNT(*) FROM ter_history"),
        "change_events": _scalar(conn, "SELECT COUNT(*) FROM change_event"),
        "caplist_isins": _scalar(
            conn, "SELECT COUNT(*) FROM isin_market_cap WHERE effective_date = ?", (caplist_date,)
        ) if caplist_date else 0,
        "caplist_effective_date": caplist_date,
        "adapter_hints": _scalar(conn, "SELECT COUNT(*) FROM scheme_adapter_hint"),
        "adapters_available": len(amc_identity.AMFI_AMC_NAME),
    }


def next_steps(inv: dict) -> list[str]:
    """Ordered, concrete remediation list — empty means the store is fully set up."""
    steps: list[str] = []

    if not inv["plans"]:
        steps.append(
            "No scheme universe loaded. Run: mf-mcp ingest-navall   "
            "(everything else resolves against it)"
        )
        return steps

    if inv["nav_end"]:
        try:
            gap = (date.today() - date.fromisoformat(inv["nav_end"])).days
        except ValueError:
            gap = 0
        if gap > _NAVALL_STALE_DAYS:
            steps.append(
                f"NAV data is {gap} days stale (latest: {inv['nav_end']}). "
                "Run: mf-mcp ingest-navall   (cheap; ideally daily via cron)"
            )

    if inv["nav_points"] and inv["nav_start"]:
        try:
            years = (date.today() - date.fromisoformat(inv["nav_start"])).days / 365.25
        except ValueError:
            years = 0.0
        if years < 3:
            steps.append(
                f"NAV history covers only {years:.1f} years (from {inv['nav_start']}). "
                "Trailing/rolling returns beyond that window are unavailable. Run: "
                "mf-mcp backfill-nav-history --from 2016-01-01   (resumable)"
            )

    if not inv["caplist_isins"]:
        steps.append(
            "No AMFI cap list — get_fund_portfolio reports market-cap allocation as "
            "unavailable. Run: mf-mcp update-caplist   (one-off, seconds)"
        )

    if not inv["portfolio_snapshots"]:
        steps.append(
            "No portfolio holdings ingested — get_fund_portfolio has nothing to return. Run: "
            "mf-mcp backfill --amc ppfas --from 2023-01-01   (repeat per AMC you care about; "
            "'mf-mcp amcs' lists the 31 supported)"
        )

    if not inv["manager_assignments"] and not inv["ter_rows"]:
        steps.append(
            "No factsheets ingested — get_fund_profile reports managers and TER as "
            "unavailable, and list_disclosure_events has no observed events. Run: "
            "mf-mcp backfill --amc <amc> --from 2023-01-01 --doc-types factsheet"
        )

    return steps


def format_report(inv: dict, steps: list[str]) -> str:
    """Human-readable `mf-mcp status` output."""
    def _mb(n: int) -> str:
        return f"{n / 1_048_576:.1f} MB" if n else "—"

    nav_window = (
        f"{inv['nav_start']} → {inv['nav_end']}" if inv["nav_start"] else "none"
    )
    lines = [
        f"Store:            {inv['data_home']}  ({_mb(inv['db_size_bytes'])})",
        f"Checked:          {datetime.now(timezone.utc).date().isoformat()}",
        "",
        "  Scheme universe (AMFI NAVAll)",
        f"    AMCs                  {inv['amcs']:>12,}",
        f"    Schemes               {inv['schemes']:>12,}   ({inv['schemes_active']:,} active)",
        f"    Plans                 {inv['plans']:>12,}",
        "",
        "  NAV history",
        f"    NAV points            {inv['nav_points']:>12,}",
        f"    Coverage              {nav_window:>12}",
        f"    Months backfilled     {inv['nav_months_ingested']:>12,}",
        "",
        "  Portfolio holdings",
        f"    Snapshots             {inv['portfolio_snapshots']:>12,}",
        f"    Schemes covered       {inv['portfolio_schemes']:>12,}",
        f"    Holding rows          {inv['holdings']:>12,}",
        "",
        "  Documents & derived facts",
        f"    Documents             {inv['documents']:>12,}",
        f"    Manager assignments   {inv['manager_assignments']:>12,}",
        f"    TER rows              {inv['ter_rows']:>12,}",
        f"    Change events         {inv['change_events']:>12,}",
        f"    Cap-list ISINs        {inv['caplist_isins']:>12,}"
        + (f"   (effective {inv['caplist_effective_date']})" if inv["caplist_effective_date"] else ""),
    ]

    if inv["portfolio_by_amc"]:
        lines += ["", "  Portfolio coverage by AMC"]
        for row in inv["portfolio_by_amc"]:
            lines.append(
                f"    {row['amc_name'][:34]:<34} {row['schemes']:>4} schemes   latest {row['latest']}"
            )

    lines += ["", "Next steps"]
    if steps:
        lines += [f"  {i}. {s}" for i, s in enumerate(steps, 1)]
    else:
        lines.append("  None — every data class is populated and current.")
    return "\n".join(lines)
