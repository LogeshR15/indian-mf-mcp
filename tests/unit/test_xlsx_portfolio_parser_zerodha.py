"""Golden test against a real, live-fetched Zerodha Nifty 50 Index Fund file (ZNFTY, August
2026) — single-sheet layout (sheet name = scheme code), "% to NAV" header, "GRAND TOTAL (AUM)"
row already fractional. Parses against xlsx_portfolio.py with zero changes."""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "zerodha_nifty50_aug2026.xlsx"


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


def test_specific_holding_pct_nav():
    r = _parse()
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert abs(icici.pct_nav - 0.0947) < 1e-6
