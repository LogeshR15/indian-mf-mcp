"""The navall-ingest -> adapter-hint -> bulk-backfill chain.

`mf-mcp backfill --amc <amc>` is the only way to load portfolio data without hand-supplying
a scheme_id, and it was inert for two independent reasons: auto_register_from_navall() was
never called by any ingest path (so scheme_adapter_hint stayed empty on every install), and
even a populated table was keyed by the AMFI-derived amc_id while the lookup passed a CLI
adapter key. Both are covered here, end to end, against real AMFI-format rows.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from indian_mf_mcp.ingest.amc_scheme_registry import get_schemes_for_amc
from indian_mf_mcp.ingest.amfi_navall import ingest_rows
from indian_mf_mcp.parsers.delimited import parse_navall

# AMFI's live NAVAll.txt layout: an AMC header line, then a scheme-type header, then rows.
_NAVALL = """Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

PPFAS Mutual Fund

Open Ended Schemes(Equity Scheme - Flexi Cap Fund)

122639;INF879O01019;INF879O01027;Parag Parikh Flexi Cap Fund - Regular Plan - Growth;89.4444;15-Sep-2026
122640;INF879O01035;INF879O01043;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;95.1234;15-Sep-2026

Nippon India Mutual Fund

Open Ended Schemes(Equity Scheme - Large Cap Fund)

100471;INF204K01059;INF204K01067;Nippon India Large Cap Fund - Regular Plan - Growth;88.1234;15-Sep-2026
"""


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        c.executescript(f.read())
    return c


@pytest.fixture()
def ingested(conn: sqlite3.Connection) -> sqlite3.Connection:
    ingest_rows(conn, parse_navall(_NAVALL), date(2026, 9, 15))
    return conn


def test_navall_ingest_registers_adapter_hints(ingested: sqlite3.Connection):
    # Regression: this table was empty after every ingest, because the auto-registration
    # function existed but nothing called it.
    n = ingested.execute("SELECT COUNT(*) FROM scheme_adapter_hint").fetchone()[0]
    assert n > 0


def test_ingest_stats_report_hint_count(conn: sqlite3.Connection):
    stats = ingest_rows(conn, parse_navall(_NAVALL), date(2026, 9, 15))
    assert stats["adapter_hints_registered"] == stats["schemes"]


def test_hints_are_keyed_by_the_amfi_derived_amc_id(ingested: sqlite3.Connection):
    rows = ingested.execute("SELECT DISTINCT amc_id FROM scheme_adapter_hint").fetchall()
    assert {r["amc_id"] for r in rows} == {
        "amc-ppfas-mutual-fund",
        "amc-nippon-india-mutual-fund",
    }


@pytest.mark.parametrize(
    "adapter_key,expected_hint",
    [("ppfas", "Parag Parikh Flexi Cap Fund"), ("nippon", "Nippon India Large Cap Fund")],
)
def test_bulk_backfill_finds_schemes_by_cli_adapter_key(
    ingested: sqlite3.Connection, adapter_key: str, expected_hint: str
):
    # The exact call `mf-mcp backfill --amc <key>` makes. It returned [] for every AMC
    # before adapter-key translation existed, so the command always exited 1.
    schemes = get_schemes_for_amc(ingested, adapter_key)
    assert [s["adapter_hint"] for s in schemes] == [expected_hint]


def test_adapter_key_and_raw_amc_id_agree(ingested: sqlite3.Connection):
    assert (
        get_schemes_for_amc(ingested, "ppfas")
        == get_schemes_for_amc(ingested, "amc-ppfas-mutual-fund")
    )


def test_adapter_with_no_ingested_schemes_returns_empty(ingested: sqlite3.Connection):
    assert get_schemes_for_amc(ingested, "hdfc") == []


def test_hint_defaults_to_the_amfi_scheme_name(ingested: sqlite3.Connection):
    # The hint is what the adapter's list_documents() matches filenames against; AMFI's
    # canonical name is the right default for most AMCs, overridable per scheme.
    row = ingested.execute(
        "SELECT sah.adapter_hint, s.name FROM scheme_adapter_hint sah "
        "JOIN scheme s USING (scheme_id) LIMIT 1"
    ).fetchone()
    assert row["adapter_hint"] == row["name"]
