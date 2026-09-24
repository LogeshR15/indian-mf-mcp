"""Golden tests against real factsheet pages (Aug 2026 editions; Nippon's is dated for 31 Jul).

Each fixture is cut from the AMC's live combined factsheet PDF and keeps two pages: the
scheme's own Flexi Cap page and one page that only MENTIONS the scheme (an index / "other funds
managed" page). Scoping must pick the first and reject the second; extraction must read the
managers and expense ratios exactly as printed. HDFC's fixture keeps only the page templates
those two pages draw (HDFC's PDF lists all 144 on every page), which is why it is small.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from indian_mf_mcp.ingest.factsheet_ingest import (
    extract_inception_date, extract_ter_from_layout, scheme_page_numbers,
)
from indian_mf_mcp.ingest.manager_extract import SINCE_INCEPTION, extract_managers_from_pages
from indian_mf_mcp.parsers.pdf_text import layout_texts, parse_pdf

FIXTURES = Path(__file__).parent.parent / "fixtures" / "factsheets"

CASES = {
    # amc: (scheme hint as AMFI spells it, own page in fixture, managers, ratios, inception)
    "ppfas": ("Parag Parikh Flexi Cap Fund", 1,
              {"Rajeev Thakkar": SINCE_INCEPTION, "Raunak Onkar": SINCE_INCEPTION,
               "Raj Mehta": "2025-09-01", "Rukun Tarachandani": "2022-05-16",
               "Tejas Soman": "2025-09-01", "Mansi Kariya": "2023-12-22",
               "Aishwarya Dhar": "2025-09-01"},
              {("BER", "Direct"): 0.53, ("BER", "Regular"): 1.05}, "2013-05-24"),
    "hdfc": ("HDFC Flexi Cap Fund", 2,
             {"Amit Ganatra": "2026-02-01", "Bhagyesh Kagalkar": "2026-08-26"},
             {("BER", "Direct"): 0.67, ("BER", "Regular"): 1.27}, "1995-01-01"),
    "nippon": ("Nippon India Flexi Cap Fund", 2,
               {"Meenakshi Dawar": "2023-01-01", "Dhrumil Shah": "2021-08-01"},
               {("BER", "Direct"): 0.47, ("BER", "Regular"): 1.50}, "2021-08-13"),
    "sbi": ("SBI Flexicap Fund", 2,
            {"Anup Upadhyay": "2024-12-01"},
            {("TER", "Direct"): 1.27, ("TER", "Regular"): 2.06,
             ("BER", "Direct"): 0.72, ("BER", "Regular"): 1.39}, "2005-09-29"),
    "kotak": ("Kotak Flexi Cap Fund", 2,
              # Kotak prints the manager's start date on a different (fund-manager) page, so
              # it is honestly unknown from the scheme page.
              {"Harsha Upadhyaya": None},
              {("BER", "Direct"): 0.61, ("BER", "Regular"): 1.43}, "2009-09-11"),
    "icici": ("ICICI Prudential Flexi Cap Fund", 2,
              {"Rajat Chandak": "2021-07-01"},
              {("BER", "Direct"): 0.65, ("BER", "Regular"): 1.37}, "2021-07-17"),
}


@pytest.fixture(scope="module", params=sorted(CASES))
def case(request):
    amc = request.param
    raw = (FIXTURES / f"{amc}_flexicap_2026.pdf").read_bytes()
    parsed = parse_pdf(raw)
    return amc, raw, parsed, CASES[amc]


def test_scopes_to_the_schemes_own_page_only(case):
    _, _, parsed, (hint, own_page, *_rest) = case
    assert scheme_page_numbers(parsed.pages, hint) == [own_page]


def test_managers_read_exactly(case):
    _, _, parsed, (hint, own_page, managers, _ratios, _inc) = case
    pages = [p for p in parsed.pages if p.page_number == own_page]
    got = {m.name: m.managing_since for m in extract_managers_from_pages(pages)}
    assert got == managers


def test_expense_ratios_typed_ter_or_ber(case):
    _, raw, parsed, (hint, own_page, _m, ratios, _inc) = case
    pages = [p for p in parsed.pages if p.page_number == own_page]
    footnotes = [ln for p in pages for ln in p.text.splitlines()]
    got = {(t.ratio_type, t.plan_type): t.ter_pct
           for t in extract_ter_from_layout(layout_texts(raw, [own_page]), footnotes)}
    assert got == pytest.approx(ratios)


def test_inception_date(case):
    _, raw, parsed, (hint, own_page, _m, _r, inception) = case
    pages = [p for p in parsed.pages if p.page_number == own_page]
    assert extract_inception_date(pages, layout_texts(raw, [own_page])) == inception


def test_other_schemes_are_not_found_on_the_page():
    """A scheme that isn't in the PDF gets no pages — never 'the whole document'."""
    parsed = parse_pdf((FIXTURES / "ppfas_flexicap_2026.pdf").read_bytes())
    assert scheme_page_numbers(parsed.pages, "Parag Parikh Liquid Fund") == []


def test_shared_page_is_split_between_schemes():
    """Kotak Jul 2026: p.40 is the Aggressive Hybrid Fund's page with a Large Cap panel below
    it; p.30 carries a stale layer listing a manager "(Effective till March 31, 2026)"."""
    from indian_mf_mcp.ingest.factsheet_ingest import scheme_segments
    from indian_mf_mcp.parsers.pdf_text import PdfPage

    parsed = parse_pdf((FIXTURES / "kotak_shared_pages_jul2026.pdf").read_bytes())

    def managers(hint):
        segs = scheme_segments(parsed.pages, hint)
        pages = [PdfPage(s.page_number, s.text, []) for s in segs]
        return {m.name: m.managing_until for m in extract_managers_from_pages(pages)}

    assert managers("Kotak Large Cap Fund") == {}
    assert managers("Kotak Aggressive Hybrid Fund") == {"Atul Bhole": None, "Abhishek Bisen": None}
    assert managers("Kotak Energy Opportunities Fund") == {
        "Mandar Pawar": None, "Abhishek Bisen": None, "Harsha Upadhyaya": "2026-03-31"}
