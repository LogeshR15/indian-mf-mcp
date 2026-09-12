"""Golden test against a real, live-fetched SBI combined-workbook file (August 2026),
proving the parser generalizes beyond PPFAS's layout: different column offsets (name column
shifted right by a scheme-code column), a percentage-points convention instead of fractional,
and "% to AUM" instead of "% to Net Assets" in the header.
"""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sbi_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes(), sheet_name="SFLEXI")


def test_reconciliation_passes_despite_different_layout():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_percentage_scale_auto_normalised_to_fractional():
    r = _parse()
    assert any("normalised to fractional" in w for w in r.warnings)
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert abs(icici.pct_nav - 0.0722) < 1e-9  # was 7.22 in the raw file


def test_holdings_extracted_with_shifted_columns():
    r = _parse()
    assert len(r.holdings) > 50
    hdfc = next(h for h in r.holdings if "HDFC Bank" in h.instrument_name)
    assert hdfc.isin == "INE040A01034"
    assert hdfc.asset_class == "equity"


def test_as_of_extracted_from_datetime_cell():
    r = _parse()
    assert "2026" in r.as_of_date_str
    assert "August" in r.as_of_date_str


def test_wrong_sheet_name_reports_zero_confidence():
    r = parse_portfolio_xlsx(FIXTURE.read_bytes(), sheet_name="NOT_A_REAL_SHEET")
    assert r.parse_confidence == 0.0
    assert r.warnings
