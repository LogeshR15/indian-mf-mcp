"""Golden test against a real, live-fetched Bajaj Finserv Mutual Fund combined monthly
portfolio workbook (August 2025) — one workbook covering all 22 schemes, no separate "Index"
sheet (the sheet key itself is the scheme's short code, e.g. "BFFLX" for Flexi Cap Fund).
Confirms the shared parser handles this AMC's layout with zero special-casing."""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.bajaj_finserv import find_sheet_code
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "bajajfinserv_flexicap_aug2025.xlsx"
SCHEME_HINT = "Bajaj Finserv Flexi Cap Fund"


def _parse():
    raw = FIXTURE.read_bytes()
    sheet = find_sheet_code(raw, SCHEME_HINT)
    return parse_portfolio_xlsx(raw, sheet_name=sheet)


def test_sheet_resolution():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, SCHEME_HINT) == "BFFLX"


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


def test_specific_holding_pct_nav():
    r = _parse()
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert abs(icici.pct_nav - 0.0221) < 1e-6
