"""Golden test against a real, live-fetched ICICI Prudential Value Fund file (August 2026),
extracted from the AMC's single combined monthly zip
(Monthly-Portfolio-Disclosure-August-2026.zip -> "ICICI Prudential Value Fund.xlsx").

Requires the shared-parser header-detection fix reported alongside the icici_prudential
adapter: ICICI's own header wording is "Company/Issuer/Instrument Name" rather than "Name of
the Instrument" / "Name of Instrument", which `_find_main_header_row` in xlsx_portfolio.py
does not currently match. See the adapter module docstring and the reported diff.
"""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "icicipru_value_fund_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes())


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 30
    icici_bank = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici_bank.isin == "INE090A01021"
    assert icici_bank.asset_class == "equity"
    assert abs(icici_bank.pct_nav - 0.0940254757637) < 1e-6
