"""Golden test against a real, live-fetched Franklin Templeton combined workbook (August
2026). This AMC puts ISIN *before* the name column (reverse of every other AMC handled so
far) and has no separate Index lookup sheet — the sheet name itself is the scheme's short
code, resolved via find_sheet_by_title.
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_by_title
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "franklin_all_aug2026.xlsx"


def test_sheet_resolver_finds_correct_code_by_title():
    raw = FIXTURE.read_bytes()
    assert find_sheet_by_title(raw, "Franklin India Large Cap Fund") == "FILCF"
    assert find_sheet_by_title(raw, "Franklin Build India Fund") == "FBIF"


def test_sheet_resolver_unknown_scheme_returns_none():
    raw = FIXTURE.read_bytes()
    assert find_sheet_by_title(raw, "Totally Unknown Fund XYZ") is None


def test_reconciliation_passes_on_debt_scheme_despite_reversed_isin_column():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="FBPF")  # Franklin India Banking & PSU Debt Fund
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_reconciliation_passes_on_equity_scheme():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="FBIF")
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01
    assert len(r.holdings) > 30
    assert all(h.asset_class == "equity" for h in r.holdings)


def test_holdings_extracted_with_isin_before_name():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="FBPF")
    assert len(r.holdings) > 30
    bond = next(h for h in r.holdings if "India Infrastructure Finance" in h.instrument_name)
    assert bond.isin == "INE787H08188"
    assert bond.asset_class == "debt"


def test_section_header_rows_not_captured_as_bogus_holdings():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="FBPF")
    names = [h.instrument_name for h in r.holdings]
    assert "Debt Instruments" not in names
    assert not any("Listed / awaiting listing" in n for n in names)
