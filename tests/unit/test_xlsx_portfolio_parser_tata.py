"""Golden test against a real, live-fetched Tata combined workbook (August 2026) — sixth
independently-verified AMC layout. Forced two genuine parser generalizations: an
"NET ASSETS" grand-total label (no "total" word at all) instead of "GRAND TOTAL", and
sub-total rows that SUFFIX "TOTAL" (e.g. "PORTFOLIO TOTAL", "EQUITY & EQUITY RELATED TOTAL")
rather than prefixing it — both of which, before the fix, doubled the reconciled total to
~200% by letting the totals fall through as fake holdings.
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tata_all_aug2026.xlsx"


def test_sheet_resolver_finds_correct_code():
    raw = FIXTURE.read_bytes()
    assert find_sheet_code(raw, "Tata Digital India Fund") == "TDIF"
    assert find_sheet_code(raw, "Tata Large & Mid Cap Fund") == "TEOF"


def test_reconciliation_passes_with_net_assets_grand_total_label():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="TDIF")
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_suffix_style_total_rows_excluded_from_holdings():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="TDIF")
    names_upper = [h.instrument_name.upper() for h in r.holdings]
    assert not any("TOTAL" in n for n in names_upper)
    assert not any(n == "NIL" for n in names_upper)


def test_holdings_extracted_with_non_adjacent_isin_column():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="TDIF")
    assert len(r.holdings) > 30
    infosys = next(h for h in r.holdings if h.instrument_name == "INFOSYS LTD")
    assert infosys.isin == "INE009A01021"
    assert infosys.asset_class == "equity"


def test_second_scheme_sheet_also_reconciles():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="TEOF")
    assert r.reconciliation_ok is True
