"""Golden test against a real, live-fetched Bank of India Mutual Fund combined workbook
(August 2026, "MONTHLY-PORTFOLIO - 31-AUGUST-2026.xlsx"). Selects the Bank of India Flexi
Cap Fund sheet (code "YB36") via the shared combined_workbook.find_sheet_code resolver,
confirming the Index-sheet convention ("Scheme Code" / "Scheme Names" header) matches the
shared helper with zero AMC-specific changes."""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "boi_flexicap_aug2026.xlsx"
SCHEME_HINT = "Bank of India Flexi Cap Fund"


def _raw() -> bytes:
    return FIXTURE.read_bytes()


def _parse():
    sheet_name = find_sheet_code(_raw(), SCHEME_HINT)
    assert sheet_name == "YB36"
    return parse_portfolio_xlsx(_raw(), sheet_name=sheet_name)


def test_sheet_resolution():
    assert find_sheet_code(_raw(), SCHEME_HINT) == "YB36"


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 50
    sbi = next(h for h in r.holdings if "State Bank of India" in h.instrument_name)
    assert sbi.isin == "INE062A01020"
    assert sbi.asset_class == "equity"


def test_specific_holding_pct_nav():
    r = _parse()
    sbi = next(h for h in r.holdings if "State Bank of India" in h.instrument_name)
    assert abs(sbi.pct_nav - 0.0501) < 1e-6
