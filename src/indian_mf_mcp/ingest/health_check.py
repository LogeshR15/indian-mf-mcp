"""Adapter health checks (spec §15, Phase 4).

"45 AMC sites, all different, all changing. Adapters will break. Generic fallback +
health checks + graceful degradation."

Design:
  - Check = call adapter.list_documents(doc_type, since=90_days_ago)
  - If raises → 'broken'
  - If returns empty list → 'degraded' (no docs found in recent window)
  - If most recent doc is stale (> expected_gap_days) → 'degraded'
  - Otherwise → 'ok'

  Crucially: does NOT fetch or parse the actual document — just checks discovery works.
  This makes checks fast (~1–2s per adapter) and safe (no large downloads).

  Results are stored in adapter_health table so get_fund_portfolio can surface warnings.
  Exit code 1 if any adapter is 'broken' (useful for CI).
"""
from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

log = logging.getLogger(__name__)

# Expected gap between consecutive documents of each type (days)
_EXPECTED_GAP: dict[str, int] = {
    "monthly_portfolio": 35,      # monthly; AMCs can be a few days late
    "fortnightly_portfolio": 20,
    "factsheet": 35,
    "addendum": 180,              # addenda are irregular; warn only if >6 months
    "sid": 365,                   # SIDs change rarely
    "kim": 365,
    "annual_report": 400,
}

_DEFAULT_WINDOW_DAYS = 90   # look back this many days when checking for recent docs


@dataclass
class HealthResult:
    amc_id: str
    doc_type: str
    status: Literal["ok", "degraded", "broken"]
    last_doc_date: Optional[date]       # most recent doc date found
    expected_gap_days: int
    actual_gap_days: Optional[int]      # days since last_doc_date at time of check
    error: Optional[str]
    checked_at: datetime

    def to_dict(self) -> dict:
        return {
            "amc_id": self.amc_id,
            "doc_type": self.doc_type,
            "status": self.status,
            "last_doc_date": self.last_doc_date.isoformat() if self.last_doc_date else None,
            "expected_gap_days": self.expected_gap_days,
            "actual_gap_days": self.actual_gap_days,
            "error": self.error,
            "checked_at": self.checked_at.isoformat(),
        }


def check_adapter(adapter, doc_type_str: str) -> HealthResult:
    """Run a health check for a single (adapter, doc_type) pair.

    Args:
        adapter: AMCAdapter instance
        doc_type_str: string value of DocType enum (e.g. 'monthly_portfolio')

    Returns:
        HealthResult
    """
    from indian_mf_mcp.ingest.amc_adapters.base import DocType

    try:
        doc_type = DocType(doc_type_str)
    except ValueError:
        return HealthResult(
            amc_id=adapter.amc_id,
            doc_type=doc_type_str,
            status="broken",
            last_doc_date=None,
            expected_gap_days=_EXPECTED_GAP.get(doc_type_str, 60),
            actual_gap_days=None,
            error=f"Unknown doc_type: {doc_type_str}",
            checked_at=datetime.now(timezone.utc),
        )

    since = date.today() - timedelta(days=_DEFAULT_WINDOW_DAYS)
    expected_gap = _EXPECTED_GAP.get(doc_type_str, 60)
    checked_at = datetime.now(timezone.utc)

    try:
        refs = adapter.list_documents(doc_type, since=since)
    except Exception as exc:
        log.warning("Adapter %s broken for %s: %s", adapter.amc_id, doc_type_str, exc)
        return HealthResult(
            amc_id=adapter.amc_id,
            doc_type=doc_type_str,
            status="broken",
            last_doc_date=None,
            expected_gap_days=expected_gap,
            actual_gap_days=None,
            error=str(exc)[:500],
            checked_at=checked_at,
        )

    if not refs:
        gap = _DEFAULT_WINDOW_DAYS  # no docs found in the window
        return HealthResult(
            amc_id=adapter.amc_id,
            doc_type=doc_type_str,
            status="degraded",
            last_doc_date=None,
            expected_gap_days=expected_gap,
            actual_gap_days=gap,
            error=f"No documents found in the last {_DEFAULT_WINDOW_DAYS} days",
            checked_at=checked_at,
        )

    # Find the most recent doc date
    dated = [r for r in refs if r.as_of_date is not None]
    if dated:
        last_date = max(r.as_of_date for r in dated)
    else:
        # No dated refs — use today as a pessimistic approximation
        last_date = date.today()

    actual_gap = (date.today() - last_date).days

    if actual_gap > expected_gap:
        status: Literal["ok", "degraded", "broken"] = "degraded"
        error = f"Last doc was {actual_gap} days ago (expected every {expected_gap} days)"
    else:
        status = "ok"
        error = None

    log.info(
        "Health %s %s %s: last=%s gap=%d",
        adapter.amc_id, doc_type_str, status,
        last_date.isoformat() if last_date else "?", actual_gap,
    )

    return HealthResult(
        amc_id=adapter.amc_id,
        doc_type=doc_type_str,
        status=status,
        last_doc_date=last_date,
        expected_gap_days=expected_gap,
        actual_gap_days=actual_gap,
        error=error,
        checked_at=checked_at,
    )


def run_health_checks(
    adapters: list,
    doc_types: list[str] | None = None,
    max_workers: int = 8,
) -> list[HealthResult]:
    """Run health checks for all (adapter × doc_type) combinations.

    Args:
        adapters: list of AMCAdapter instances
        doc_types: list of doc_type strings to check; defaults to portfolio + factsheet
        max_workers: thread pool size

    Returns:
        List of HealthResult, one per (adapter, doc_type) pair
    """
    if doc_types is None:
        doc_types = ["monthly_portfolio", "factsheet"]

    tasks = [
        (adapter, doc_type)
        for adapter in adapters
        for doc_type in doc_types
    ]

    results: list[HealthResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(check_adapter, adapter, doc_type): (adapter.amc_id, doc_type)
            for adapter, doc_type in tasks
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                amc_id, doc_type = futures[future]
                log.error("Health check task failed: %s %s — %s", amc_id, doc_type, exc)
                results.append(HealthResult(
                    amc_id=amc_id,
                    doc_type=doc_type,
                    status="broken",
                    last_doc_date=None,
                    expected_gap_days=_EXPECTED_GAP.get(doc_type, 60),
                    actual_gap_days=None,
                    error=f"Unexpected error: {exc}",
                    checked_at=datetime.now(timezone.utc),
                ))

    # Sort by status severity (broken first), then amc_id
    _order = {"broken": 0, "degraded": 1, "ok": 2}
    results.sort(key=lambda r: (_order[r.status], r.amc_id, r.doc_type))
    return results


def save_health_results(conn: sqlite3.Connection, results: list[HealthResult]) -> None:
    """Upsert health check results into the adapter_health table."""
    for r in results:
        conn.execute(
            """INSERT OR REPLACE INTO adapter_health
               (amc_id, doc_type, checked_at, status, last_doc_date, actual_gap_days, error)
               VALUES (?,?,?,?,?,?,?)""",
            (
                r.amc_id,
                r.doc_type,
                r.checked_at.isoformat(),
                r.status,
                r.last_doc_date.isoformat() if r.last_doc_date else None,
                r.actual_gap_days,
                r.error,
            ),
        )
    conn.commit()


def format_health_table(results: list[HealthResult]) -> str:
    """Format health results as a human-readable ASCII table for CLI output."""
    if not results:
        return "No health checks to display."

    lines = [
        f"{'AMC':<25} {'DocType':<22} {'Status':<10} {'LastDoc':<12} {'Gap':>5}  Error",
        "-" * 100,
    ]
    for r in results:
        status_icon = {"ok": "✓", "degraded": "⚠", "broken": "✗"}.get(r.status, "?")
        last = r.last_doc_date.isoformat() if r.last_doc_date else "unknown"
        gap = str(r.actual_gap_days) + "d" if r.actual_gap_days is not None else "?"
        error = (r.error or "")[:50]
        lines.append(
            f"{r.amc_id:<25} {r.doc_type:<22} {status_icon} {r.status:<8} {last:<12} {gap:>5}  {error}"
        )
    return "\n".join(lines)
