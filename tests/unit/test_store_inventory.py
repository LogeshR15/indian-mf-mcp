"""Store inventory and the next-steps advisor behind `mf-mcp status`."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from indian_mf_mcp.ingest.amfi_navall import ingest_rows
from indian_mf_mcp.parsers.delimited import parse_navall
from indian_mf_mcp.store import inventory

_NAVALL = """Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

PPFAS Mutual Fund

Open Ended Schemes(Equity Scheme - Flexi Cap Fund)

122640;INF879O01035;INF879O01043;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;95.1234;15-Sep-2026
"""


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        c.executescript(f.read())
    return c


class TestEmptyStore:
    def test_counts_are_zero(self, conn):
        inv = inventory.collect(conn)
        assert inv["schemes"] == 0 and inv["plans"] == 0 and inv["nav_points"] == 0

    def test_first_step_is_the_scheme_universe(self, conn):
        steps = inventory.next_steps(inventory.collect(conn))
        assert len(steps) == 1
        assert "mf-mcp ingest-navall" in steps[0]

    def test_no_other_advice_before_the_universe_exists(self, conn):
        # Telling someone to backfill portfolios before any scheme exists is noise that
        # buries the one step that actually unblocks them.
        steps = inventory.next_steps(inventory.collect(conn))
        assert not any("backfill" in s for s in steps)

    def test_report_renders(self, conn):
        inv = inventory.collect(conn)
        out = inventory.format_report(inv, inventory.next_steps(inv))
        assert "Next steps" in out and "ingest-navall" in out


class TestPopulatedStore:
    def test_counts_reflect_ingest(self, conn):
        ingest_rows(conn, parse_navall(_NAVALL), date.today())
        inv = inventory.collect(conn)
        assert inv["amcs"] == 1 and inv["schemes"] == 1 and inv["plans"] == 1

    def test_advises_caplist_portfolio_and_factsheets(self, conn):
        ingest_rows(conn, parse_navall(_NAVALL), date.today())
        steps = " | ".join(inventory.next_steps(inventory.collect(conn)))
        assert "update-caplist" in steps
        assert "backfill --amc" in steps
        assert "factsheet" in steps

    def test_thin_nav_history_is_called_out(self, conn):
        ingest_rows(conn, parse_navall(_NAVALL), date.today())
        steps = " | ".join(inventory.next_steps(inventory.collect(conn)))
        assert "backfill-nav-history" in steps

    def test_stale_navall_is_called_out(self, conn):
        ingest_rows(conn, parse_navall(_NAVALL), date.today())
        old = (date.today() - timedelta(days=30)).isoformat()
        conn.execute("UPDATE nav_point SET date = ?", (old,))
        steps = " | ".join(inventory.next_steps(inventory.collect(conn)))
        assert "stale" in steps and "ingest-navall" in steps

    def test_fresh_navall_is_not_flagged_stale(self, conn):
        ingest_rows(conn, parse_navall(_NAVALL), date.today())
        conn.execute("UPDATE nav_point SET date = ?", (date.today().isoformat(),))
        steps = " | ".join(inventory.next_steps(inventory.collect(conn)))
        assert "stale" not in steps

    def test_caplist_advice_disappears_once_populated(self, conn):
        ingest_rows(conn, parse_navall(_NAVALL), date.today())
        conn.execute(
            "INSERT INTO isin_market_cap (isin, market_cap, effective_date) "
            "VALUES ('INE040A01034','large','2026-07-01')"
        )
        steps = " | ".join(inventory.next_steps(inventory.collect(conn)))
        assert "update-caplist" not in steps
