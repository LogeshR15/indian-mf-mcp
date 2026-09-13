"""Golden test against a real, live-fetched 360 ONE Mutual Fund combined workbook (May 2026).
Like Franklin Templeton, this AMC has no separate "Index" lookup sheet at all -- each
per-scheme sheet's own row 1 carries the full scheme name as free text, resolved via
find_sheet_by_title. Header wording is "Rounded % to Net Assets" (already a recognised
xlsx_portfolio.py header alias) and values are already fractional -- no shared-parser change
of any kind was needed for this AMC.
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_by_title
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "360one_flexicap_may2026.xlsx"


def test_sheet_resolver_finds_correct_sheet_by_title():
    raw = FIXTURE.read_bytes()
    assert find_sheet_by_title(raw, "360 ONE Flexicap Fund") == "Flexicap Fund"
    assert find_sheet_by_title(raw, "360 ONE Dynamic Bond Fund") == "Dynamic Bond"


def test_sheet_resolver_unknown_scheme_returns_none():
    raw = FIXTURE.read_bytes()
    assert find_sheet_by_title(raw, "Totally Unknown Fund XYZ") is None


def test_reconciliation_passes():
    raw = FIXTURE.read_bytes()
    sheet = find_sheet_by_title(raw, "360 ONE Flexicap Fund")
    r = parse_portfolio_xlsx(raw, sheet_name=sheet)
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    raw = FIXTURE.read_bytes()
    sheet = find_sheet_by_title(raw, "360 ONE Flexicap Fund")
    r = parse_portfolio_xlsx(raw, sheet_name=sheet)
    assert len(r.holdings) > 30
    assert all(h.asset_class == "equity" or h.instrument_name for h in r.holdings)


def test_specific_holding_isin_and_pct_nav():
    raw = FIXTURE.read_bytes()
    sheet = find_sheet_by_title(raw, "360 ONE Flexicap Fund")
    r = parse_portfolio_xlsx(raw, sheet_name=sheet)
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici.isin == "INE090A01021"
    assert abs(icici.pct_nav - 0.048419446196406146) < 1e-6
