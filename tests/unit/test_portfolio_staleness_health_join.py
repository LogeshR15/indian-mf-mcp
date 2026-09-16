"""get_fund_portfolio's staleness warning must actually find its adapter_health row.

`mf-mcp health --save` writes rows keyed by the adapter's own amc_id ("amc-ppfas"), but the
tool looks up the AMFI-derived scheme.amc_id ("amc-ppfas-mutual-fund"). The two never
matched, so the health-aware branch was unreachable and every stale portfolio fell through
to the generic message — a silent failure no test caught.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from indian_mf_mcp.tools.get_fund_portfolio import _staleness_warning

_PPFAS_DB_ID = "amc-ppfas-mutual-fund"      # what scheme.amc_id holds
_PPFAS_ADAPTER_ID = "amc-ppfas"             # what adapter_health is keyed by


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        c.executescript(f.read())
    return c


def _seed_health(conn, amc_id: str, status: str = "broken", error: str = "404 on listing"):
    conn.execute(
        "INSERT INTO adapter_health (amc_id, doc_type, checked_at, status, last_doc_date, "
        "actual_gap_days, error) VALUES (?, 'monthly_portfolio', ?, ?, ?, ?, ?)",
        (amc_id, "2026-09-16T00:00:00Z", status, "2026-01-31", 228, error),
    )


def _stale_date() -> str:
    return (date.today() - timedelta(days=200)).isoformat()


def test_health_row_is_found_via_the_db_amc_id(conn):
    _seed_health(conn, _PPFAS_ADAPTER_ID)
    warning = _staleness_warning(conn, _PPFAS_DB_ID, _stale_date())
    assert warning is not None
    assert "Adapter health" in warning
    assert "broken" in warning
    assert "404 on listing" in warning


def test_health_row_written_under_the_bare_adapter_key_is_also_found(conn):
    _seed_health(conn, "ppfas")
    warning = _staleness_warning(conn, _PPFAS_DB_ID, _stale_date())
    assert warning is not None and "Adapter health" in warning


def test_ok_status_does_not_append_the_error_clause(conn):
    _seed_health(conn, _PPFAS_ADAPTER_ID, status="ok", error="")
    warning = _staleness_warning(conn, _PPFAS_DB_ID, _stale_date())
    assert "ok" in warning and "—" not in warning.split("Adapter health")[1]


def test_falls_back_to_the_generic_message_without_a_health_row(conn):
    warning = _staleness_warning(conn, _PPFAS_DB_ID, _stale_date())
    assert warning is not None
    assert "Adapter health" not in warning
    assert "backfill-portfolio" in warning


def test_amc_with_no_adapter_still_gets_the_generic_message(conn):
    warning = _staleness_warning(conn, "amc-canara-robeco-mutual-fund", _stale_date())
    assert warning is not None and "Adapter health" not in warning


def test_fresh_snapshot_produces_no_warning(conn):
    _seed_health(conn, _PPFAS_ADAPTER_ID)
    assert _staleness_warning(conn, _PPFAS_DB_ID, date.today().isoformat()) is None
