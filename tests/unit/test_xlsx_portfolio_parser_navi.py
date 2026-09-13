"""Golden test against a real, live-fetched Navi Flexi Cap Fund file (August 2026) — the
current-layout one-file-per-scheme .xlsx, confirming the shared parser generalizes to Navi's
scheme workbooks without any AMC-specific special-casing."""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "navi_flexicap_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes())


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 50
    icici = next(h for h in r.holdings if "ICICI BANK" in h.instrument_name.upper())
    assert icici.isin == "INE090A01021"
    assert icici.asset_class == "equity"


def test_specific_holding_pct_nav():
    r = _parse()
    icici = next(h for h in r.holdings if "ICICI BANK" in h.instrument_name.upper())
    assert abs(icici.pct_nav - 0.0381) < 1e-6
