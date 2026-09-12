"""Golden test against a real SID, live-fetched from AMFI's spages/<id>.pdf mapping."""
from pathlib import Path

from indian_mf_mcp.parsers.pdf_text import find_heading_pages, parse_pdf, search

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
