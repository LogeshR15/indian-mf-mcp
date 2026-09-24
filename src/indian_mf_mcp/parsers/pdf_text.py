"""PDF text extraction + SEBI-standard heading detection + keyword-scoped search.

Ground-truthed against a real SID fetched live from AMFI's spages/<id>.pdf mapping
(spec §3.1, §7): headings are standardised by SEBI's Master Circular format but do not always
render as clean standalone lines in extracted text (umbrella SIDs covering multiple schemes
repeat heading words in body prose too) — so heading detection is a best-effort label per page,
never a guarantee, and `get_document`'s primary retrieval path is keyword search over page
text, not strict section slicing.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pypdf

# SEBI Master Circular standard SID/KIM headings (spec §7).
SEBI_HEADINGS = [
    "Investment Objective", "Investment Strategy", "Asset Allocation Pattern",
    "Risk Factors", "Fund Manager", "Load Structure", "Expenses of the Scheme",
]

_HEADING_RES = {h: re.compile(rf"^\s*{re.escape(h)}\b", re.IGNORECASE | re.MULTILINE) for h in SEBI_HEADINGS}


@dataclass
class PdfPage:
    page_number: int  # 1-indexed
    text: str
    headings: list[str]  # SEBI headings that appear to start a line on this page


@dataclass
class PdfParseResult:
    pages: list[PdfPage]
    page_count: int
    parse_confidence: float
    warnings: list[str]


def parse_pdf(raw: bytes) -> PdfParseResult:
    warnings: list[str] = []
    try:
        reader = pypdf.PdfReader(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        return PdfParseResult(pages=[], page_count=0, parse_confidence=0.0,
                               warnings=[f"failed to open PDF: {exc}"])

    pages: list[PdfPage] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            text = ""
            warnings.append(f"page {i + 1}: extraction failed: {exc}")
        headings = [h for h, rx in _HEADING_RES.items() if rx.search(text)]
        pages.append(PdfPage(page_number=i + 1, text=text, headings=headings))

    non_empty = sum(1 for p in pages if p.text.strip())
    confidence = 0.0 if not pages else non_empty / len(pages)
    if confidence < 1.0 and pages:
        warnings.append(f"{len(pages) - non_empty} of {len(pages)} pages yielded no extractable "
                         f"text (possibly scanned images) — not OCR'd, per MVP scope")

    return PdfParseResult(pages=pages, page_count=len(pages), parse_confidence=confidence,
                           warnings=warnings)


def layout_texts(raw: bytes, page_numbers: list[int]) -> dict[int, str]:
    """Layout-preserving text for selected pages (1-indexed), keyed by page number.

    Factsheets are multi-column. pypdf's default extraction reads column by column, so a label
    and its value end up far apart ("Regular Plan:\nDirect Plan:" ... later "1.05%* 0.53%*").
    Layout text keeps each visual line together ("Direct Plan:   0.53%*"), which is what
    label-then-value figures (expense ratios, turnover) need. pdfplumber is used rather than
    pypdf's own layout mode because HDFC builds every factsheet page from Form XObject
    templates, and pypdf's layout mode returns an empty string for all of them (Aug 2026, all
    144 pages); pdfminer, under pdfplumber, reads into the templates. It is ~0.4 s/page, so it
    is only computed for the pages a caller asks for, alongside — not instead of — the default
    text."""
    import pdfplumber

    out: dict[int, str] = {}
    try:
        pdf = pdfplumber.open(io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        return out
    with pdf:
        for n in page_numbers:
            if 1 <= n <= len(pdf.pages):
                try:
                    out[n] = pdf.pages[n - 1].extract_text(layout=True) or ""
                except Exception:  # noqa: BLE001
                    out[n] = ""
    return out


def classify_parse_status(result: PdfParseResult) -> str:
    """Maps a PdfParseResult to a document.parse_status value:
      - 'unparseable'          the PDF failed to open, or opened with zero pages
      - 'unparseable_scanned'  pages exist but none yielded extractable text — almost
                                certainly a scanned/image-only document, which this project
                                deliberately does not OCR (spec §7 MVP scope)
      - 'parsed'                at least one page yielded text
    A distinct, filterable status for the scanned case (rather than folding it into the
    same generic 'unparseable' as a corrupt file) matters because it's a different, common,
    and expected failure mode — callers may want to surface it differently to the user.
    """
    if not result.pages:
        return "unparseable"
    if result.parse_confidence == 0.0:
        return "unparseable_scanned"
    return "parsed"


def find_heading_pages(result: PdfParseResult, heading: str) -> list[int]:
    return [p.page_number for p in result.pages if heading in p.headings]


def search(result: PdfParseResult, query: str, max_chars: int = 15000) -> list[dict]:
    """Keyword search across page texts. Returns matched pages (whole-page granularity,
    the smallest unit pypdf gives us cheaply) ranked by keyword hit count, truncated to
    max_chars total. Each item carries page_number and the (possibly truncated) text.
    """
    terms = [t.lower() for t in re.findall(r"[a-zA-Z0-9']+", query)] if query else []
    scored = []
    for p in result.pages:
        if not terms:
            scored.append((0, p))
            continue
        low = p.text.lower()
        score = sum(low.count(t) for t in terms)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda sp: sp[0], reverse=True)

    out = []
    budget = max_chars
    for score, p in scored:
        if budget <= 0:
            break
        text = p.text if len(p.text) <= budget else p.text[:budget] + "…[truncated]"
        out.append({"page_number": p.page_number, "headings": p.headings, "text": text,
                     "match_score": score})
        budget -= len(text)
    return out
