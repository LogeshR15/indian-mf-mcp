"""Tests for parse_portfolio_xls() — the legacy binary Excel (.xls / BIFF) counterpart of
parse_portfolio_xlsx(), added per spec §7's explicit call for XLS support (some AMCs, e.g.
PPFAS's own combined file per the spec's ground-truthing, still publish .xls).

Unlike the per-AMC golden-fixture tests elsewhere in this suite, no real downloaded .xls AMC
file is used here — acquiring one requires live discovery work equivalent to onboarding a new
AMC, which is out of scope for this parser-level change. Instead these build a small,
realistic-shaped workbook with xlwt (real BIFF bytes, not a stand-in) covering the same
sections/columns/reconciliation rules the XLSX golden fixtures exercise, so the shared
row-walking core (_parse_rows, shared with parse_portfolio_xlsx) is verified against the real
binary format rather than assumed to work by analogy.
"""
from __future__ import annotations

import io

import pytest
import xlwt

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xls


def _build_xls(rows: list[list], sheet_name: str = "Sheet1") -> bytes:
    wb = xlwt.Workbook()
    ws = wb.add_sheet(sheet_name)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            if val is not None:
                ws.write(r, c, val)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_HEADER = ["Name of the Instrument", "ISIN", "Industry", "Quantity",
           "Market Value (Rs. in Lakhs)", "% to Net Assets"]


def _standard_rows() -> list[list]:
    return [
        _HEADER,
        ["Equity & Equity Related", None, None, None, None, None],
        ["(a) Listed / awaiting listing on Stock Exchange", None, None, None, None, None],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 1000, 1500.0, 0.5],
        ["Infosys Limited", "INE009A01021", "IT - Software", 800, 1200.0, 0.4],
        ["Money Market Instruments", None, None, None, None, None],
        ["TREPS", None, None, None, 300.0, 0.1],
        ["Grand Total", None, None, None, 3000.0, 1.0],
    ]


def test_reconciliation_passes():
    raw = _build_xls(_standard_rows())
    r = parse_portfolio_xls(raw)
    assert r.reconciliation_ok is True
    assert r.parse_confidence == 1.0
    assert not r.warnings


def test_holdings_extracted_with_isin_and_asset_class():
    raw = _build_xls(_standard_rows())
    r = parse_portfolio_xls(raw)
    assert len(r.holdings) == 3
    hdfc = next(h for h in r.holdings if h.instrument_name == "HDFC Bank Limited")
    assert hdfc.isin == "INE040A01034"
    assert hdfc.asset_class == "equity"
    assert hdfc.pct_nav == pytest.approx(0.5)


def test_reconciliation_fails_when_grand_total_off():
    rows = _standard_rows()
    rows[-1] = ["Grand Total", None, None, None, 3000.0, 0.90]  # 10% short of 100%
    raw = _build_xls(rows)
    r = parse_portfolio_xls(raw)
    assert r.reconciliation_ok is False
    assert r.parse_confidence == 0.5


def test_percentage_point_scale_normalised():
    """Same convention-detection rule as the XLSX parser: a grand total > 5 means the file
    reports percentage points (7.5), not fractional (0.075) — must normalise both ways."""
    rows = [
        _HEADER,
        ["Equity & Equity Related", None, None, None, None, None],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 1000, 1500.0, 75.0],
        ["Money Market Instruments", None, None, None, None, None],
        ["TREPS", None, None, None, 500.0, 25.0],
        ["Grand Total", None, None, None, 2000.0, 100.0],
    ]
    raw = _build_xls(rows)
    r = parse_portfolio_xls(raw)
    assert r.reconciliation_ok is True
    hdfc = next(h for h in r.holdings if h.instrument_name == "HDFC Bank Limited")
    assert hdfc.pct_nav == pytest.approx(0.75)


def test_sheet_selection_for_combined_workbook():
    wb = xlwt.Workbook()
    ws1 = wb.add_sheet("Index")
    ws1.write(0, 0, "Short Name")
    ws1.write(0, 1, "Scheme Name")
    ws1.write(1, 0, "CODE1")
    ws1.write(1, 1, "Scheme One")
    ws2 = wb.add_sheet("CODE1")
    for r, row in enumerate(_standard_rows()):
        for c, val in enumerate(row):
            if val is not None:
                ws2.write(r, c, val)
    buf = io.BytesIO()
    wb.save(buf)
    raw = buf.getvalue()

    r = parse_portfolio_xls(raw, sheet_name="CODE1")
    assert r.reconciliation_ok is True
    assert len(r.holdings) == 3


def test_unknown_sheet_name_reports_failure_not_exception():
    raw = _build_xls(_standard_rows())
    r = parse_portfolio_xls(raw, sheet_name="DoesNotExist")
    assert r.parse_confidence == 0.0
    assert any("not found" in w for w in r.warnings)


def test_malformed_xls_reports_zero_confidence_not_exception():
    r = parse_portfolio_xls(b"not a real xls file")
    assert r.parse_confidence == 0.0
    assert r.holdings == []
    assert r.warnings


def test_blank_cells_do_not_leak_as_empty_string_isin():
    """xlrd represents an unset cell as '' rather than openpyxl's None — must be normalised
    to None before reaching the shared row-walking core, or a blank ISIN cell would be
    stored as the empty string instead of null."""
    raw = _build_xls(_standard_rows())
    r = parse_portfolio_xls(raw)
    treps = next(h for h in r.holdings if h.instrument_name == "TREPS")
    assert treps.isin is None


def test_sheet_count_reported_for_single_sheet_workbook():
    raw = _build_xls(_standard_rows())
    r = parse_portfolio_xls(raw)
    assert r.sheet_count == 1


def test_sheet_count_reported_for_multi_sheet_workbook():
    wb = xlwt.Workbook()
    for name in ("Index", "CODE1"):
        ws = wb.add_sheet(name)
        ws.write(0, 0, "placeholder")
    buf = io.BytesIO()
    wb.save(buf)
    r = parse_portfolio_xls(buf.getvalue(), sheet_name="CODE1")
    assert r.sheet_count == 2
