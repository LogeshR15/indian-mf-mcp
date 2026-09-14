"""Golden test against a real, live-fetched HSBC Flexi Cap Fund file (August 2026). One file
per scheme (PPFAS/Mirae/DSP/Union layout), but HSBC's own header/footer wording needed three
genuinely general fixes in xlsx_portfolio.py (see hsbc.py's module docstring for the full
detail): a "Percentage to Net Assets" spelled-out %-header synonym, a "Total Net Assets as on
<date>" prefix match for the grand-total row, and a "Scheme Riskometer" footer-block stop so
the scheme-name-as-label footer row isn't misread as a holding.
"""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "hsbc_flexicap_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes())


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.005


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 50
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici.isin == "INE090A01021"
    assert icici.asset_class == "equity"


def test_real_holding_isin_and_weight():
    r = _parse()
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert abs(icici.pct_nav - 0.0449) < 1e-4


def test_no_footer_rows_leak_into_holdings():
    r = _parse()
    # The "Scheme Riskometer" footer block repeats the scheme's own name as a label with no
    # ISIN/quantity/value — must not appear as a spurious holding after the fix.
    assert not any(h.instrument_name == "HSBC Flexi Cap Fund" for h in r.holdings)
