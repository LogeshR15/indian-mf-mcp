"""Scheme-name matching and sheet selection against real AMC files.

- normalize_scheme_name: AMFI's spelling vs each AMC's own ("Flexi Cap" / "Flexicap",
  "UTI - Flexi Cap Fund." / "UTI Flexi Cap Fund").
- find_sole_holdings_sheet: one-file-per-scheme AMCs whose file carries extra non-holdings
  sheets (HDFC derivatives, Taurus performance, HSBC notes/disclaimer, DSP empty Sheet1). Must
  pick the holdings sheet there, and refuse a real combined workbook or a mismatched scheme.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import openpyxl
import pytest

from indian_mf_mcp.ingest.amc_adapters.base import normalize_scheme_name
from indian_mf_mcp.ingest.amc_adapters.combined_workbook import (
    find_sheet_code, find_sole_holdings_sheet,
)
from indian_mf_mcp.ingest.amc_adapters.icici_prudential import _extract_scheme
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_normalize_ignores_spacing_and_punctuation_only():
    assert normalize_scheme_name("Kotak Flexi Cap Fund") == normalize_scheme_name("Kotak Flexicap Fund")
    assert normalize_scheme_name("UTI - Flexi Cap Fund.") == normalize_scheme_name("UTI Flexi Cap Fund")
    assert normalize_scheme_name("Kotak Flexi Cap Fund") != normalize_scheme_name("Kotak Multicap Fund")


def test_index_lookup_matches_flexicap_spelling():
    """Kotak's Index sheet says "Kotak Flexicap Fund"; AMFI's hint says "Kotak Flexi Cap Fund"."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Index"
    ws.append(["Scheme Code", "Scheme Name"])
    ws.append(["SMC", "Kotak Multicap Fund"])
    ws.append(["SEF", "Kotak Flexicap Fund"])
    buf = io.BytesIO()
    wb.save(buf)
    assert find_sheet_code(buf.getvalue(), "Kotak Flexi Cap Fund") == "SEF"


def test_icici_zip_member_matches_across_spellings():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ICICI Prudential Flexicap Fund..xlsx", b"flexi")
        zf.writestr("ICICI Prudential Multicap Fund.xlsx", b"multi")
    assert _extract_scheme(buf.getvalue(), "ICICI Prudential Flexi Cap fund") == b"flexi"


@pytest.mark.parametrize("fixture, hint, expected", [
    ("hdfc_flexicap_aug2026.xlsx", "HDFC Flexi Cap Fund", "HDFCEQ"),
    ("taurus_flexicap_aug2026.xlsx", "Taurus Flexi Cap Fund", "TSS"),
    ("hsbc_flexicap_aug2026.xlsx", "HSBC Flexi Cap Fund", None),  # sheet code checked below
    ("dsp_flexicap_aug2026.xlsx", "DSP Flexi Cap Fund", None),
])
def test_sole_holdings_sheet_on_real_single_scheme_files(fixture, hint, expected):
    raw = (FIXTURES / fixture).read_bytes()
    sheet = find_sole_holdings_sheet(raw, hint)
    assert sheet is not None
    if expected is not None:
        assert sheet == expected
    r = parse_portfolio_xlsx(raw, sheet_name=sheet)
    assert r.reconciliation_ok is True
    assert sum(1 for h in r.holdings if h.isin) > 20


def test_sole_holdings_sheet_refuses_mismatched_scheme():
    raw = (FIXTURES / "hdfc_flexicap_aug2026.xlsx").read_bytes()
    assert find_sole_holdings_sheet(raw, "Taurus Flexi Cap Fund") is None


def test_sole_holdings_sheet_refuses_combined_workbook():
    """Many sheets with holdings is a combined workbook: never pick one without a resolver."""
    raw = (FIXTURES / "nippon_all_aug2026.xls").read_bytes()
    assert find_sole_holdings_sheet(raw, "Nippon India Flexi Cap Fund") is None
