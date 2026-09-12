"""Golden test against a real, live-fetched Motilal Oswal combined workbook (August 2026),
plus the shared combined-workbook Index-sheet resolver (also used by SBI's layout, with
different column positions — proving the resolver isn't hardcoded to one AMC's index shape).
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "motilal_aug2026.xlsx"


def test_sheet_resolver_finds_correct_code():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, "Motilal Oswal Flexi Cap Fund") == "YO08"
    assert find_sheet_code(raw, "Motilal Oswal Midcap Fund") == "YO07"


def test_sheet_resolver_unknown_scheme_returns_none():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, "Totally Unknown Fund XYZ") is None


def test_reconciliation_passes_on_selected_sheet():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="YO08")
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted_with_leading_srno_column():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="YO08")
    assert len(r.holdings) > 30
    kalyan = next(h for h in r.holdings if "Kalyan Jewellers" in h.instrument_name)
    assert kalyan.isin == "INE303R01014"
    assert kalyan.asset_class == "equity"
