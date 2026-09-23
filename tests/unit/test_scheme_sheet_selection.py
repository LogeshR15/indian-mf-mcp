"""Scheme-name matching: AMFI's spelling vs each AMC's own ("Flexi Cap" / "Flexicap",
"UTI - Flexi Cap Fund." / "UTI Flexi Cap Fund").
"""
from __future__ import annotations

import io
import zipfile

import openpyxl

from indian_mf_mcp.ingest.amc_adapters.base import normalize_scheme_name
from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.ingest.amc_adapters.icici_prudential import _extract_scheme


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

