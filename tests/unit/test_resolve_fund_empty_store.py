"""resolve_fund must distinguish 'no such fund' from 'nothing ingested yet'.

On an un-ingested store every query returns zero candidates, and the generic warning told
the user to check their spelling — sending them to debug their input instead of running
the one command that fixes it.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from indian_mf_mcp.ingest.amfi_navall import ingest_rows
from indian_mf_mcp.parsers.delimited import parse_navall
from indian_mf_mcp.tools.resolve_fund import resolve_fund

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


def _warnings(payload: dict) -> str:
    return " ".join(payload.get("_warnings", []))


class TestEmptyStore:
    def test_warning_names_the_empty_store_not_the_spelling(self, conn):
        w = _warnings(resolve_fund(conn, "parag parikh flexi cap"))
        assert "no schemes" in w.lower()
        assert "spelling" not in w.lower()

    def test_warning_names_the_command_that_fixes_it(self, conn):
        w = _warnings(resolve_fund(conn, "anything"))
        assert "mf-mcp setup" in w or "mf-mcp ingest-navall" in w

    def test_candidates_are_still_empty(self, conn):
        assert resolve_fund(conn, "anything")["anything"] == []

    def test_batch_query_warns_once_not_per_query(self, conn):
        # A 20-fund batch against an empty store should not emit 20 identical paragraphs.
        payload = resolve_fund(conn, ["a", "b", "c"])
        assert len(payload["_warnings"]) == 1

    def test_every_query_key_is_still_present_in_a_batch(self, conn):
        payload = resolve_fund(conn, ["a", "b", "c"])
        assert payload["a"] == [] and payload["b"] == [] and payload["c"] == []


class TestPopulatedStore:
    @pytest.fixture()
    def ingested(self, conn):
        ingest_rows(conn, parse_navall(_NAVALL), date(2026, 9, 15))
        return conn

    def test_a_real_miss_still_gets_the_spelling_hint(self, ingested):
        w = _warnings(resolve_fund(ingested, "definitely not a fund"))
        assert "spelling" in w.lower()
        assert "no schemes" not in w.lower()

    def test_a_hit_produces_no_warning(self, ingested):
        payload = resolve_fund(ingested, "Parag Parikh Flexi Cap")
        assert "_warnings" not in payload
        assert payload["Parag Parikh Flexi Cap"][0]["scheme_id"].startswith("scheme-")
