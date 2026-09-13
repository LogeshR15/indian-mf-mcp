"""Golden test against a real, live-fetched Axis Midcap Fund file (August 2026) — confirms the
parser generalizes to Axis's layout (fractional "% to Net Assets", GRAND TOTAL row, standard
Equity & Equity related section labelling) without any AMC-specific special-casing."""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "axis_midcap_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes())


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 50


def test_known_holding_isin_and_pct_nav():
    r = _parse()
    federal_bank = next(h for h in r.holdings if "Federal Bank" in h.instrument_name)
    assert federal_bank.isin == "INE171A01029"
    assert abs(federal_bank.pct_nav - 0.044) < 1e-6
    assert federal_bank.asset_class == "equity"
