from datetime import date
from pathlib import Path

from indian_mf_mcp.ingest.amfi_nav_history import month_chunks
from indian_mf_mcp.parsers.delimited import parse_amfi_date, parse_navall

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_nav_history.txt"


def test_history_layout_columns_resolved_by_name_not_position():
    """The history report orders columns differently from NAVAll.txt (ISINs come after
    Plan/Option, and the name column is 'NAV Name')."""
    rows = parse_navall(FIXTURE.read_text())
    assert len(rows) == 5

    row = rows[0]
    assert row.scheme_code == "122639"
    assert row.nav == 90.7896
    assert row.date == "01-Sep-2026"
    assert row.plan_raw == "Direct Plan"
    assert row.option_raw == "Growth"
    assert row.isin_div_payout_or_growth == "INF879O01027"
    assert row.isin_div_reinvestment is None
    assert row.scheme_name == "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"
    assert all(r.nav is not None for r in rows)


def test_history_section_headers_still_parsed():
    rows = parse_navall(FIXTURE.read_text())
    assert rows[0].sub_category == "Flexi Cap Fund"
    assert rows[0].amc_name == "Parag Parikh Financial Advisory Services Private Limited"
    assert rows[-1].amc_name == "Taurus Mutual Fund"


def test_one_scheme_code_yields_one_row_per_day():
    rows = parse_navall(FIXTURE.read_text())
    dates = [r.date for r in rows if r.scheme_code == "122639"]
    assert dates == ["01-Sep-2026", "02-Sep-2026", "03-Sep-2026"]


def test_parse_amfi_date():
    assert parse_amfi_date("12-Sep-2026") == "2026-09-12"
    assert parse_amfi_date(" 01-Jan-2019 ") == "2019-01-01"
    assert parse_amfi_date("2026-09-12") is None
    assert parse_amfi_date(None) is None


def test_month_chunks_clip_to_bounds():
    chunks = list(month_chunks(date(2026, 1, 15), date(2026, 3, 3)))
    assert chunks == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 3)),
    ]


def test_month_chunks_cross_year_boundary():
    assert list(month_chunks(date(2025, 12, 30), date(2026, 1, 2))) == [
        (date(2025, 12, 30), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 2)),
    ]


def test_month_chunks_single_day():
    assert list(month_chunks(date(2026, 5, 7), date(2026, 5, 7))) == [
        (date(2026, 5, 7), date(2026, 5, 7))
    ]
