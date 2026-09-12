"""Golden test against a real, live-fetched DSP Flexi Cap Fund file (August 2026) — same
PPFAS/Mirae-style layout (Sr.No. leading column, fractional %NAV), confirming the parser
handles a repeat of a known-good shape without any AMC-specific special-casing."""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "dsp_flexicap_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes())


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 50
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici.isin == "INE090A01021"
    assert icici.asset_class == "equity"


def test_as_of_extracted():
    r = _parse()
    assert "2026" in r.as_of_date_str
    assert "August" in r.as_of_date_str
