"""Golden test against a real, live-fetched Nippon India combined workbook (August 2026).
Two quirks worth pinning: the file has a `.xls` extension but is real OOXML/XLSX content by
magic bytes (must be opened via BytesIO, not a bare path — openpyxl gates on extension for
path input), and its per-scheme sheets list ISIN *before* the instrument name in the header
row (every other AMC does the reverse) — the column-detecting parser handles both with zero
special-casing.
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.parsers.sniff import FormatKind, sniff
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "nippon_all_aug2026.xls"


def test_magic_bytes_say_xlsx_despite_xls_extension():
    assert sniff(FIXTURE.read_bytes()) == FormatKind.XLSX


def test_sheet_resolver_handles_headerless_index_sheet():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, "Nippon India Consumption Fund") == "ME"
    assert find_sheet_code(raw, "Nippon India Growth Mid Cap Fund") == "GF"


def test_sheet_resolver_unknown_scheme_returns_none():
    assert find_sheet_code(FIXTURE.read_bytes(), "Totally Unknown Fund XYZ") is None


def test_reconciliation_passes_with_isin_before_name_header():
    r = parse_portfolio_xlsx(FIXTURE.read_bytes(), sheet_name="ME")
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted_correctly():
    r = parse_portfolio_xlsx(FIXTURE.read_bytes(), sheet_name="ME")
    assert len(r.holdings) > 30
    mm = next(h for h in r.holdings if "Mahindra & Mahindra" in h.instrument_name)
    assert mm.isin == "INE101A01026"
    assert mm.asset_class == "equity"
