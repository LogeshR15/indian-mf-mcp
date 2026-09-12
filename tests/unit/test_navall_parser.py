from pathlib import Path

from indian_mf_mcp.parsers.delimited import parse_navall

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_navall.txt"


def test_parses_expected_row_count():
    rows = parse_navall(FIXTURE.read_text())
    assert len(rows) == 5


def test_taxonomy_split_from_section_header():
    rows = parse_navall(FIXTURE.read_text())
    ppfas_row = next(r for r in rows if "Parag Parikh" in r.scheme_name and "Direct" in r.scheme_name)
    assert ppfas_row.scheme_type == "Open Ended Schemes"
    assert ppfas_row.category == "Equity Scheme"
    assert ppfas_row.sub_category == "Flexi Cap Fund"
    assert ppfas_row.amc_name == "Parag Parikh Financial Advisory Services Private Limited"


def test_isin_and_nav_parsed():
    rows = parse_navall(FIXTURE.read_text())
    row = next(r for r in rows if r.scheme_code == "122639")
    assert row.isin_div_payout_or_growth == "INF879O01027"
    assert row.nav == 75.1234
    assert row.date == "10-Sep-2026"


def test_second_category_section_switches_taxonomy():
    rows = parse_navall(FIXTURE.read_text())
    axis_row = next(r for r in rows if "Axis" in r.scheme_name)
    assert axis_row.sub_category == "Mid Cap Fund"
