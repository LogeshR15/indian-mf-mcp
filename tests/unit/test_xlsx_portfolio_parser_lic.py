"""Golden test against a real, live-fetched LIC MF Flexi Cap Fund file (August 2026), retrieved
via the AJAX form chain documented in lic.py's module docstring. One workbook per scheme
(sheet named after the scheme code, "LEEQTF"), already-fractional pct_nav, "GRAND TOTAL" row —
parses cleanly with zero AMC-specific changes to the shared parser."""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "lic_flexicap_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes())


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 30
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici.isin == "INE090A01021"
    assert icici.asset_class == "equity"


def test_pct_nav_value():
    r = _parse()
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert abs(icici.pct_nav - 0.0471) < 1e-6
