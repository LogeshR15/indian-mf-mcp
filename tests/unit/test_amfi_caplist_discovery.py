"""AMFI cap-list discovery and the ranked-layout parser.

AMFI's 2026 site rebuild 404'd the single hardcoded spreadsheet URL, which took
get_fund_portfolio's whole market-cap section offline. The replacement scrapes AMFI's
listing page for every published half-yearly file, so the point-in-time join in
get_market_cap() has more than one version to choose between.

The golden fixture is the real 30 Jun 2026 file trimmed to its first 261 companies —
enough to cross both SEBI rank boundaries (100 / 250).
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from indian_mf_mcp.ingest.amfi_caplist import (
    _cap_for_rank,
    _CAP_FILE_RE,
    _effective_date_from_name,
    _parse_caplist_xlsx,
    get_market_cap,
)

FIXTURE = pathlib.Path("tests/fixtures/amfi_caplist_30jun2026.xlsx")


@pytest.fixture()
def raw() -> bytes:
    return FIXTURE.read_bytes()


class TestRankClassification:
    @pytest.mark.parametrize(
        "rank,expected",
        [(1, "large"), (100, "large"), (101, "mid"), (250, "mid"), (251, "small"), (5000, "small")],
    )
    def test_sebi_boundaries(self, rank, expected):
        assert _cap_for_rank(rank) == expected


class TestRankedParser:
    def test_parses_every_company_row(self, raw):
        _, rows = _parse_caplist_xlsx(raw)
        assert len(rows) == 261

    def test_effective_date_comes_from_the_title_row(self, raw):
        effective_date, _ = _parse_caplist_xlsx(raw)
        assert effective_date == "2026-06-30"

    def test_bucket_counts_follow_the_rank_rule(self, raw):
        _, rows = _parse_caplist_xlsx(raw)
        caps = [c for _, _, c in rows]
        assert caps.count("large") == 100
        assert caps.count("mid") == 150
        assert caps.count("small") == 11

    def test_top_rank_is_large_cap(self, raw):
        _, rows = _parse_caplist_xlsx(raw)
        assert rows[0][0] == "INE002A01018"       # Reliance
        assert rows[0][2] == "large"

    def test_rank_boundary_flips_bucket(self, raw):
        _, rows = _parse_caplist_xlsx(raw)
        assert rows[99][2] == "large"
        assert rows[100][2] == "mid"

    def test_every_row_has_a_valid_isin(self, raw):
        _, rows = _parse_caplist_xlsx(raw)
        assert all(r[0].startswith("IN") and len(r[0]) == 12 for r in rows)

    def test_company_names_are_captured(self, raw):
        _, rows = _parse_caplist_xlsx(raw)
        assert rows[0][1] == "Reliance Industries Ltd"

    def test_header_and_title_rows_are_not_ingested(self, raw):
        _, rows = _parse_caplist_xlsx(raw)
        assert not any("Sr. No." in r[1] or "Average Market" in r[1] for r in rows)


class TestLinkDiscovery:
    """The href patterns actually present on AMFI's listing page."""

    @pytest.mark.parametrize(
        "href",
        [
            'href="https://portal.amfiindia.com/spages/AverageMarketCapitalization30Jun2026.xlsx"',
            'href="/Themes/Theme1/downloads/AverageMarketCapitalizationoflistedcompaniesduringthesixmonthsended31Dec2022.xlsx"',
            # Strapi-era name, and note the trailing space before the closing quote —
            # AMFI really does emit this, and requiring .xlsx" dropped a whole half-year.
            'href="https://www.amfiindia.com/uploads/Average_Market_Capitalization_30_Jun2024_2a1ab4c1d8.xlsx "',
        ],
    )
    def test_matches_every_filename_vintage(self, href):
        assert _CAP_FILE_RE.findall(href)

    def test_ignores_the_pdf_twin(self):
        # Each period is published as both .pdf and .xlsx; only the spreadsheet parses.
        assert not _CAP_FILE_RE.findall(
            'href="https://www.amfiindia.com/Themes/Theme1/downloads/AverageMarketCapitalization30Jun2024.pdf"'
        )

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("AverageMarketCapitalization30Jun2026.xlsx", "2026-06-30"),
            ("...sixmonthsended31Dec2022.xlsx", "2022-12-31"),
            ("Average_Market_Capitalization_30_Jun2024_2a1ab4c1d8.xlsx", "2024-06-30"),
            ("... six months ended 30 June 2026", "2026-06-30"),
        ],
    )
    def test_effective_date_parsed_from_name(self, name, expected):
        assert _effective_date_from_name(name) == expected

    def test_returns_none_when_no_date_present(self):
        assert _effective_date_from_name("AverageMarketCapitalization.xlsx") is None


class TestPointInTimeLookup:
    """Loading the full archive is the whole point: one version cannot be point-in-time."""

    @pytest.fixture()
    def conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        with open("src/indian_mf_mcp/store/schema.sql") as f:
            c.executescript(f.read())
        # Same ISIN, different bucket in successive half-years — a real promotion.
        for eff, cap in [("2023-06-30", "small"), ("2024-06-30", "mid"), ("2025-06-30", "large")]:
            c.execute(
                "INSERT INTO isin_market_cap (isin, market_cap, effective_date) VALUES (?,?,?)",
                ("INE999A01015", cap, eff),
            )
        return c

    def test_uses_the_list_in_force_at_the_snapshot_date(self, conn):
        assert get_market_cap(conn, "INE999A01015", "2023-12-31") == "small"
        assert get_market_cap(conn, "INE999A01015", "2024-12-31") == "mid"
        assert get_market_cap(conn, "INE999A01015", "2025-12-31") == "large"

    def test_never_reclassifies_with_a_future_list(self, conn):
        # The bug a single-version store guarantees: a 2023 portfolio shown as large-cap.
        assert get_market_cap(conn, "INE999A01015", "2023-07-01") == "small"

    def test_returns_none_before_any_list_existed(self, conn):
        assert get_market_cap(conn, "INE999A01015", "2022-01-01") is None
