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


# --- current 8-column layout (Plan/Option as dedicated columns) ---------------------------

FIXTURE_V2 = Path(__file__).parent.parent / "fixtures" / "sample_navall_v2.txt"


def test_v2_nav_and_date_read_from_last_two_columns():
    rows = parse_navall(FIXTURE_V2.read_text())
    assert len(rows) == 6
    row = next(r for r in rows if r.scheme_code == "122639")
    assert row.nav == 75.1234
    assert row.date == "10-Sep-2026"
    assert all(r.nav is not None for r in rows)


def test_v2_plan_and_option_columns_captured():
    rows = parse_navall(FIXTURE_V2.read_text())
    row = next(r for r in rows if r.scheme_code == "122640")
    assert row.plan_raw == "Regular Plan"
    assert row.option_raw == "IDCW Payout"
    assert row.scheme_name == "Parag Parikh Flexi Cap Fund"


def test_v2_blank_plan_option_columns_become_none():
    rows = parse_navall(FIXTURE_V2.read_text())
    row = next(r for r in rows if r.scheme_code == "118779")
    assert row.plan_raw is None
    assert row.option_raw is None


def test_legacy_layout_has_no_plan_option_columns():
    rows = parse_navall(FIXTURE.read_text())
    assert all(r.plan_raw is None and r.option_raw is None for r in rows)


def test_isin_placeholders_normalized_to_none():
    rows = parse_navall(FIXTURE_V2.read_text())
    assert next(r for r in rows if r.scheme_code == "122639").isin_div_reinvestment is None
    assert next(r for r in rows if r.scheme_code == "118779").isin_div_reinvestment is None
    assert next(r for r in rows if r.scheme_code == "101235").isin_div_reinvestment == "INF179K01174"
