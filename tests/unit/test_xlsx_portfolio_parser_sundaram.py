"""Golden test against a real, live-fetched Sundaram combined workbook (August 2026).
Forced two genuine parser generalizations: an Index sheet using "ACRONYM" as its code
column header (rather than "fund/scheme/short code"), and a main-table header labeled
"% of Net Asset" (rather than "% to Net Assets"/"% to AUM"/"% to NAV" seen elsewhere).
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sundaram_all_aug2026.xlsx"


def test_sheet_resolver_finds_correct_code_via_acronym_column():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, "Sundaram Large And Mid Cap Fund") == "MULTIP"
    assert find_sheet_code(raw, "Sundaram Mid Cap Fund") == "MIDCAP"


def test_sheet_resolver_unknown_scheme_returns_none():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, "Totally Unknown Fund XYZ") is None


def test_reconciliation_passes_with_pct_of_net_asset_header():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="MULTIP")
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01
    assert not r.warnings  # already fractional, no scale-normalisation needed


def test_holdings_extracted_with_isin_before_name_shifted_layout():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="MULTIP")
    assert len(r.holdings) > 30
    radico = next(h for h in r.holdings if "Radico Khaitan" in h.instrument_name)
    assert radico.isin == "INE944F01028"
    assert radico.asset_class == "equity"


def test_cash_and_derivative_sections_segregated():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="MULTIP")
    classes = {h.asset_class for h in r.holdings}
    assert "cash" in classes  # "Cash and Other Net Current Assets" line
