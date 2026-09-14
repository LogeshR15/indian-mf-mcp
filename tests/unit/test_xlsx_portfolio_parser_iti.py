"""Golden test against a real, live-fetched ITI Mutual Fund combined workbook (August 2026).
No parser generalisation was needed: the Index sheet's "Short Name"/"Scheme Name" columns and
the main table's "% to Net\\n Assets" header (embedded newline) both already match existing
keyword/substring detection in the shared parser.
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "iti_flexicap_aug2026.xlsx"


def test_sheet_resolver_finds_correct_code_via_short_name_column():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, "ITI Flexi Cap Fund") == "ITIFCF"
    assert find_sheet_code(raw, "ITI Small Cap Fund") == "ITISCF"


def test_sheet_resolver_unknown_scheme_returns_none():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, "Totally Unknown Fund XYZ") is None


def test_reconciliation_passes():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="ITIFCF")
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted_with_real_isin_and_weight():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="ITIFCF")
    assert len(r.holdings) > 30
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici.isin == "INE090A01021"
    assert abs(icici.pct_nav - 0.048) < 0.001
    assert icici.asset_class == "equity"
