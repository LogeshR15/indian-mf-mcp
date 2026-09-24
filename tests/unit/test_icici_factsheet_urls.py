"""Pure URL-building tests for ICICI Prudential factsheet discovery (no network).

Expected URLs are real entries from ICICI's own Historical Factsheets listing, captured
2026-09-23 (see the adapter module docstring)."""
from datetime import date

from indian_mf_mcp.ingest.amc_adapters.icici_prudential import (
    FACTSHEET_HOST,
    _financial_year,
    factsheet_candidate_urls,
)


def test_financial_year_april_boundary():
    assert _financial_year(2026, 3) == "2025-2026"
    assert _financial_year(2026, 4) == "2026-2027"


def test_current_convention_is_tried_first():
    urls = factsheet_candidate_urls(date(2026, 8, 31))
    assert urls[0] == f"{FACTSHEET_HOST}/2026-2027/Complete Factsheet August 2026.pdf"


def test_candidates_cover_observed_irregular_names():
    cases = {
        date(2026, 4, 30): "2025-2026/Complete Factsheet April 2026.pdf",  # prev-FY folder
        date(2025, 2, 28): "2024-2025/Complete Factsheet Feb 2025.pdf",
        date(2024, 3, 31): "2023-2024/complete-Factsheet-for-march-2024.pdf",
        date(2022, 5, 31): "2022-2023/complete-factsheet-for-may.pdf",
        date(2019, 11, 30): "2019-2020/complete-factsheet-for-november-2019.pdf",
        date(2014, 9, 30): "2014-2015/complete-factsheet-for-sep-2014.pdf",
        date(2001, 1, 31): "2000-2001/complete-factsheet-for-jan-2001.pdf",
    }
    for as_of, tail in cases.items():
        assert f"{FACTSHEET_HOST}/{tail}" in factsheet_candidate_urls(as_of), as_of


def test_candidates_are_unique():
    urls = factsheet_candidate_urls(date(2025, 3, 31))
    assert len(urls) == len(set(urls)) == 12
    # "May" is both the full and abbreviated name: duplicates are dropped, not re-probed.
    may = factsheet_candidate_urls(date(2025, 5, 31))
    assert len(may) == len(set(may)) == 8
