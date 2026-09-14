"""Golden test against a real SID, live-fetched from AMFI's spages/<id>.pdf mapping."""
from pathlib import Path

from indian_mf_mcp.parsers.pdf_text import (
    PdfPage,
    PdfParseResult,
    classify_parse_status,
    find_heading_pages,
    parse_pdf,
    search,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_sid.pdf"


def _parse():
    return parse_pdf(FIXTURE.read_bytes())


def test_parses_all_pages_with_high_confidence():
    r = _parse()
    assert r.page_count == 47
    assert r.parse_confidence == 1.0
    assert not r.warnings


def test_known_sebi_headings_detected():
    r = _parse()
    assert find_heading_pages(r, "Investment Objective")
    assert find_heading_pages(r, "Risk Factors")


def test_keyword_search_ranks_by_hit_count_and_respects_budget():
    r = _parse()
    results = search(r, "investment strategy fund manager", max_chars=2000)
    assert results
    assert sum(len(item["text"]) for item in results) <= 2000 + len("…[truncated]")
    # results should be sorted descending by match_score
    scores = [item["match_score"] for item in results]
    assert scores == sorted(scores, reverse=True)


def test_malformed_pdf_reports_zero_confidence_not_exception():
    from indian_mf_mcp.parsers.pdf_text import parse_pdf as pp
    result = pp(b"not a real pdf")
    assert result.parse_confidence == 0.0
    assert result.pages == []
    assert result.warnings


class TestClassifyParseStatus:
    def test_real_document_is_parsed(self):
        assert classify_parse_status(_parse()) == "parsed"

    def test_pdf_that_failed_to_open_is_unparseable(self):
        result = parse_pdf(b"not a real pdf")
        assert classify_parse_status(result) == "unparseable"

    def test_pages_present_but_zero_extractable_text_is_unparseable_scanned(self):
        """The scanned/image-only case: pypdf opens the PDF and returns real pages, but
        none of them yield any text (this project deliberately never OCRs, per MVP
        scope) — must be distinguished from a plain 'unparseable' corrupt-file failure."""
        result = PdfParseResult(
            pages=[PdfPage(page_number=1, text="", headings=[]),
                   PdfPage(page_number=2, text="   ", headings=[])],
            page_count=2, parse_confidence=0.0, warnings=[],
        )
        assert classify_parse_status(result) == "unparseable_scanned"

    def test_partial_text_extraction_still_counts_as_parsed(self):
        """Even a low, nonzero confidence (some pages scanned, some not) is still
        meaningfully 'parsed' — only true zero-confidence means nothing was recovered."""
        result = PdfParseResult(
            pages=[PdfPage(page_number=1, text="Investment Objective: ...", headings=[]),
                   PdfPage(page_number=2, text="", headings=[])],
            page_count=2, parse_confidence=0.5, warnings=[],
        )
        assert classify_parse_status(result) == "parsed"
