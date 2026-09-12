"""Golden test against a real, live-fetched Baroda BNP Paribas combined workbook (Aug 2026),
served with a `.xls` extension but real OOXML content (same quirk as Nippon India), plus the
shared combined-workbook resolver's "Short Name" header synonym (this AMC's Index sheet
calls its code column "Short Name", not "short code"/"scheme code").
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "barodabnp_largecap_aug2026.xlsx"


def test_sheet_resolver_handles_short_name_header():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, "Baroda BNP Paribas Large Cap Fund") == "T0ME04"
    assert find_sheet_code(raw, "Baroda BNP Paribas Mid Cap Fund") == "T0ME02"


def test_reconciliation_passes():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="T0ME04")
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted_and_derivatives_segregated():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="T0ME04")
    assert len(r.holdings) > 30
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici.isin == "INE090A01021"
    assert icici.asset_class == "equity"
    assert len(r.derivatives) > 0
