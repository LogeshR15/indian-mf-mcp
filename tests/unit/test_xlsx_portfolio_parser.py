"""Golden test against a real, live-fetched AMC portfolio file: PPFAS Flexi Cap, Aug 2026
(PPFCF_PPFAS_Monthly_Portfolio_Report_August_31_2026.xlsx). Pins parser behavior against
ground truth, not a hand-built fixture.
"""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ppfcf_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes())


def test_reconciliation_passes_within_tolerance():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01
    assert r.parse_confidence == 1.0


def test_holding_count_and_isin_present():
    r = _parse()
    assert len(r.holdings) > 100
    hdfc = next(h for h in r.holdings if h.instrument_name == "HDFC Bank Limited")
    assert hdfc.isin == "INE040A01034"
    assert hdfc.asset_class == "equity"
    assert hdfc.pct_nav > 0


def test_foreign_and_reit_and_cash_asset_classes_segregated():
    r = _parse()
    classes = {h.asset_class for h in r.holdings}
    assert "foreign" in classes
    assert "reit" in classes
    assert "cash" in classes
    alphabet = next(h for h in r.holdings if "Alphabet" in h.instrument_name)
    assert alphabet.asset_class == "foreign"
    assert alphabet.isin.startswith("US")


def test_derivatives_segregated_never_in_holdings():
    r = _parse()
    assert len(r.derivatives) > 0
    holding_names = {h.instrument_name for h in r.holdings}
    deriv_names = {d.instrument_name for d in r.derivatives}
    assert holding_names.isdisjoint(deriv_names)
    short_positions = [d for d in r.derivatives if d.direction == "Short"]
    assert len(short_positions) > 0
    assert all(d.quantity < 0 for d in short_positions)


def test_benchmark_and_as_of_extracted():
    r = _parse()
    assert r.benchmark_name == "Nifty 500 TRI"
    assert "2026" in r.as_of_date_str


def test_net_receivables_row_captured_with_null_isin():
    r = _parse()
    nr = next(h for h in r.holdings if "Net Receivables" in h.instrument_name)
    assert nr.isin is None
    assert nr.pct_nav < 0  # net payable this month
