"""Golden test against a real, live-fetched Union Flexi Cap Fund file (August 2026) — matches
the PPFAS/Mirae/DSP layout exactly, confirming the parser generalization holds without any
AMC-specific special-casing."""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "union_flexicap_aug2026.xlsx"


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


def test_percentage_points_normalised():
    r = _parse()
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert abs(icici.pct_nav - 0.0658) < 1e-6
