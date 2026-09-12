"""Golden test against a real, live-fetched UTI Flexi Cap file (August 2026) — a third,
independently-verified layout: ISIN column not adjacent to name/qty/value at all (column 7
vs columns 0-4), "% TO NAV" header wording, percentage-points scale, and no explicit "GRAND
TOTAL" %-figure at all (falls back to self-summed reconciliation).
"""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "uti_flexicap_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes())


def test_reconciliation_via_self_summed_fallback():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01
    assert any("self-summed" in w for w in r.warnings)


def test_non_adjacent_isin_column_still_resolved():
    r = _parse()
    icici = next(h for h in r.holdings if "ICICI BANK" in h.instrument_name)
    assert icici.isin == "INE090A01021"
    assert icici.asset_class == "equity"


def test_net_current_assets_captured_as_cash_not_dropped():
    r = _parse()
    nca = next(h for h in r.holdings if "NET CURRENT ASSETS" in h.instrument_name)
    assert nca.asset_class == "cash"
    assert nca.pct_nav is not None and nca.pct_nav > 0


def test_percentage_points_normalised():
    r = _parse()
    icici = next(h for h in r.holdings if "ICICI BANK" in h.instrument_name)
    assert abs(icici.pct_nav - 0.0595) < 1e-6  # was 5.95 in the raw file
