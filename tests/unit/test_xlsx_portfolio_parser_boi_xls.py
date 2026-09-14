"""Golden test against a real, live-fetched Bank of India Mutual Fund combined workbook —
but the September 2021 one, which is genuine legacy binary Excel (.xls / BIFF), not .xlsx.
Companion to test_xlsx_portfolio_parser_boi.py (the 2026 .xlsx golden test): proves
parse_portfolio_xls and combined_workbook.find_sheet_code's format-agnostic dispatch both
work against real historical bytes, not just a synthetic fixture.

The Index sheet in this 2021-era file names the scheme "Flexi Cap Fund" rather than the
2026 file's "Bank of India Flexi Cap Fund" — the AMC's own naming drifted over the years,
which is exactly why find_sheet_code does substring matching rather than requiring an exact
scheme_hint match.
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.parsers.sniff import FormatKind, sniff
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xls

FIXTURE = Path(__file__).parent.parent / "fixtures" / "boi_flexicap_sep2021_legacy.xls"
SCHEME_HINT = "Flexi Cap Fund"


def _raw() -> bytes:
    return FIXTURE.read_bytes()


def _parse():
    sheet_name = find_sheet_code(_raw(), SCHEME_HINT)
    assert sheet_name == "YB36"
    return parse_portfolio_xls(_raw(), sheet_name=sheet_name)


def test_fixture_is_genuine_legacy_biff_not_xlsx():
    assert sniff(_raw()) == FormatKind.XLS_BIFF


def test_sheet_resolution_works_via_xlrd_not_just_openpyxl():
    assert find_sheet_code(_raw(), SCHEME_HINT) == "YB36"


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.005


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 50
    sbi = next(h for h in r.holdings if "State Bank of India" in h.instrument_name)
    assert sbi.isin == "INE062A01020"
    assert sbi.asset_class == "equity"


def test_specific_holding_pct_nav():
    r = _parse()
    sbi = next(h for h in r.holdings if "State Bank of India" in h.instrument_name)
    assert abs(sbi.pct_nav - 0.0493) < 1e-6
