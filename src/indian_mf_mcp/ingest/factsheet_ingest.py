"""Phase 3: Factsheet ingestion + manager/TER extraction + ChangeEvent detection.

Factsheets are the primary monthly source for:
  - Current fund manager name(s) and managing-since date       (spec §3.3)
  - Current TER (direct & regular)                             (spec §3.8)
  - Benchmark name (cross-check against portfolio footer)      (spec §3.1)

Pipeline per document:
  1. fetch → blob store (sha256, never evicted)
  2. parse PDF → PdfParseResult
  3. extract_managers_from_pages → list[ExtractedManager]
  4. extract_ter_from_pages → list[TEREntry]
  5. diff against previous factsheet to detect manager/TER changes → ChangeEvents
  6. upsert Manager + ManagerAssignment + TERHistory + ChangeEvent rows

This module does NOT do AMC-specific factsheet URL discovery — that lives in the per-AMC
adapter's `list_documents(DocType.FACTSHEET, ...)` method. Here we only process bytes
that the caller has already fetched.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from indian_mf_mcp.ingest.document_ingest import ingest_document_from_url
from indian_mf_mcp.ingest.manager_extract import (
    SINCE_INCEPTION, ExtractedManager, extract_managers_from_pages,
)
from indian_mf_mcp.ingest.amc_adapters.base import normalize_scheme_name
from indian_mf_mcp.parsers.pdf_text import (
    PdfPage, PdfParseResult, classify_parse_status, layout_texts, parse_pdf,
)
from indian_mf_mcp.store import blobstore
from indian_mf_mcp.store import manager_repository as mrep
from indian_mf_mcp.parsers.sniff import FormatKind, sniff

# ---------------------------------------------------------------------------
# TER extraction
# ---------------------------------------------------------------------------

# Patterns observed across AMC factsheets:
#   "Expense Ratio (TER): Direct Plan: 0.63% | Regular Plan: 1.31%"
#   "Total Expense Ratio (TER) Direct: 0.63% p.a."
#   "TER: Direct: 0.51%  Regular: 1.20%"
_TER_BLOCK_RE = re.compile(
    r"(?:total\s+)?expense\s+ratio|TER",
    re.IGNORECASE,
)
_DIRECT_TER_RE = re.compile(
    r"direct\s*(?:plan\s*)?[:\-]?\s*([\d]+\.[\d]+)\s*%",
    re.IGNORECASE,
)
_REGULAR_TER_RE = re.compile(
    r"regular\s*(?:plan\s*)?[:\-]?\s*([\d]+\.[\d]+)\s*%",
    re.IGNORECASE,
)


@dataclass
class TEREntry:
    plan_type: str       # 'Direct' | 'Regular'
    ter_pct: float       # e.g. 0.63
    raw_text: str
    confidence: str      # 'high' | 'low'
    # 'TER' (total expense ratio) or 'BER' (base expense ratio). Since April 2026 (SEBI MF
    # Regulations 2026, reg. 66(7)) factsheets print the BER, which excludes transaction
    # costs and taxes; the TER moves to an annexure or the AMC website. They are different
    # numbers and must never be stored as each other.
    ratio_type: str = "TER"
    typed_by_columns: bool = False  # True when a "TER  BER" column heading fixed the type


def extract_ter_from_pages(pages: list) -> list[TEREntry]:
    """Extract TER figures from PDF pages. Returns at most one Direct + one Regular entry."""
    direct: TEREntry | None = None
    regular: TEREntry | None = None

    for page in pages:
        text = page.text
        if not _TER_BLOCK_RE.search(text):
            continue

        # search within the block for direct/regular figures
        dm = _DIRECT_TER_RE.search(text)
        rm = _REGULAR_TER_RE.search(text)

        if dm and direct is None:
            ctx_start = max(0, dm.start() - 80)
            ctx_end = min(len(text), dm.end() + 40)
            direct = TEREntry(
                plan_type="Direct",
                ter_pct=float(dm.group(1)),
                raw_text=text[ctx_start:ctx_end],
                confidence="high",
            )
        if rm and regular is None:
            ctx_start = max(0, rm.start() - 80)
            ctx_end = min(len(text), rm.end() + 40)
            regular = TEREntry(
                plan_type="Regular",
                ter_pct=float(rm.group(1)),
                raw_text=text[ctx_start:ctx_end],
                confidence="high",
            )
        if direct and regular:
            break

    return [e for e in [direct, regular] if e is not None]


_TER_HEADING_RE = re.compile(r"expense\s+ratio|\bTER\b", re.IGNORECASE)
_BASE_RATIO_RE = re.compile(r"base\s+expense\s+ratio|\bBER\b", re.IGNORECASE)
# A plan label at the START of a (layout) line, then its figure within a short reach. Matching
# at line start matters: Nippon's regular-plan line reads "Regular/Other than Direct   1.50",
# which a match-anywhere "Direct" would read as the Direct figure.
_PLAN_LINE_RE = re.compile(
    r"^\s*(direct|regular|other)\b[^\d\n]{0,160}?(\d{1,2}\.\d{1,4})\s*%?", re.IGNORECASE)
# "Label: 1.27%" anywhere on the line — HDFC prints both plans on one line:
# "Regular: 1.27% Direct: 0.67%".
_PLAN_COLON_RE = re.compile(r"\b(direct|regular|other)(?:\s+plan)?\s*:\s*(\d{1,2}\.\d{1,4})\s*%",
                            re.IGNORECASE)


def _plan(label: str) -> str:
    """ICICI labels the regular plan "Other" ("Other : 1.37% p. a.  Direct : 0.65% p. a.")."""
    return "Direct" if label.lower() == "direct" else "Regular"
# How many layout lines below an "Expense Ratio" heading the Direct/Regular figures may sit,
# and how far past them a "Base Expense Ratio" caption may still label them.
_TER_WINDOW = 6
_CAPTION_REACH = 3


_BASE_NEAR_HEADING_RE = re.compile(r"expense\s+ratio.{0,300}?base\s+expense\s+ratio",
                                   re.IGNORECASE | re.DOTALL)
_PCT_RE = re.compile(r"(\d{1,2}\.\d{1,4})\s*%")
_TER_BER_COLS_RE = re.compile(r"\b(TER|BER)\b")


def _column_labels(line: str) -> list[str]:
    """["TER", "BER"] (in printed order) when a heading line names both as columns."""
    cols = _TER_BER_COLS_RE.findall(line)
    return cols if len(set(cols)) == 2 else []


# A footnote marker right after the heading ("Expense Ratio**", "Expense Ratio2", "…Ratio^").
_HEADING_MARKER_RE = re.compile(r"expense\s+ratio\s*([*^¥#$@†]{1,3}|\d)(?=\s|$)", re.IGNORECASE)


def _footnote_says_base(marker: str, footnotes: list[str]) -> bool:
    """Does the footnote the heading points at describe the figures as the Base Expense Ratio?
    Kotak (Aug 2026): "Expense Ratio**" … "**The expense ratio disclosed above represents the
    Base Expense Ratio (BER) …". Footnotes are read from default text, where they stay whole."""
    if marker.isdigit():
        # A superscript digit only counts at the start of a footnote line (SBI "2Total Expense
        # Ratio (TER) = …"); anywhere else it is just a number.
        return any(ln.lstrip().startswith(marker) and _BASE_RATIO_RE.search(" ".join(footnotes[i:i + 3]))
                   for i, ln in enumerate(footnotes))
    # Symbol markers can start mid-line and the footnote wraps (Kotak: "…refer page 163-167 &
    # 182-186. **The expense ratio disclosed above represents the Base" / "Expense Ratio (BER)").
    text = " ".join(footnotes)
    for m in re.finditer(re.escape(marker) + r"(?![*^¥#$@†])", text):
        if _BASE_RATIO_RE.search(text[m.end():m.end() + 200]):
            return True
    return False


def extract_ter_from_layout(layout: dict[int, str], footnotes: list[str] | None = None) -> list[TEREntry]:
    """Expense ratios from layout text of the scheme's pages: a "Direct"/"Regular" label with
    its figure on the same visual line, within a few lines under an "Expense Ratio"/"TER"
    heading. The heading anchor keeps other "Direct Plan …" lines (returns tables) out; the
    same-line rule keeps a label from borrowing a neighbouring column's number.

    Each figure is typed TER or BER:
      - a heading naming both as columns ("TER  BER") types each column in printed order;
      - otherwise "Base Expense Ratio" in the heading or a caption just below the figures
        makes it BER, and anything else is TER.
      PPFAS (Aug 2026):  "Expense Ratio" / "Regular Plan:  1.05%*" / "Direct Plan:  0.53%*" /
                         "Base Expense Ratio (As on last business day of the month)"   -> BER
      Nippon (Jul 2026): "Base Expense Ratio^" / "Regular/Other than Direct  1.50" /
                         "Direct  0.47"                                                -> BER
      HDFC (Aug 2026):   "EXPENSE RATIO" / "Base expense Ratio Including …" /
                         "Regular: 1.27% Direct: 0.67%"                                -> BER
      SBI (Aug 2026):    "TER   BER" / "Regular  2.06%  1.39%" / "Direct  1.27%  0.72%"
                                                                          -> TER and BER
      Kotak (Aug 2026):  "Expense Ratio**" / "Regular Plan:  1.43%" / "Direct Plan:  0.61%",
                         footnote "**… represents the Base Expense Ratio (BER) …"      -> BER
      ICICI (Aug 2026):  "Base Expense Ratio @@ :" / "Other : 1.37% p. a." /
                         "Direct : 0.65% p. a."  ("Other" is the regular plan)       -> BER"""
    found: dict[tuple[str, str], TEREntry] = {}

    def keep(plan: str, rtype: str, v: float, line: str) -> None:
        if (plan, rtype) not in found and 0 < v < 5:
            found[(plan, rtype)] = TEREntry(plan_type=plan, ter_pct=v,
                                            raw_text=" ".join(line.split())[:200],
                                            confidence="high", ratio_type=rtype)

    for n in sorted(layout):
        lines = layout[n].splitlines()
        for i, line in enumerate(lines):
            if not _TER_HEADING_RE.search(line):
                continue
            block = lines[i:i + 1 + _TER_WINDOW]
            heads = [ln for ln in block if ln.strip()][:3]
            columns = next((c for c in (_column_labels(ln) for ln in heads) if c), [])
            caption = lines[i:i + 1 + _TER_WINDOW + _CAPTION_REACH]
            marker = _HEADING_MARKER_RE.search(line)
            ratio_type = "BER" if (
                any(_BASE_RATIO_RE.search(c) for c in caption)
                or (marker and _footnote_says_base(marker.group(1), footnotes or []))
            ) else "TER"
            for cand in block:
                if columns:
                    m = re.match(r"^\s*(direct|regular|other)\b", cand, re.IGNORECASE)
                    if m:
                        pcts = _PCT_RE.findall(cand[m.end():])
                        for rtype, value in zip(columns, pcts):
                            keep(_plan(m.group(1)), rtype, float(value), cand)
                            if (_plan(m.group(1)), rtype) in found:
                                found[(_plan(m.group(1)), rtype)].typed_by_columns = True
                    continue
                hits = [(m.group(1), m.group(2)) for m in _PLAN_COLON_RE.finditer(cand)]
                if not hits:
                    m = _PLAN_LINE_RE.match(cand)
                    hits = [(m.group(1), m.group(2))] if m else []
                for label, value in hits:
                    keep(_plan(label), ratio_type, float(value), cand)
            if {k[0] for k in found} == {"Direct", "Regular"}:
                break
        if {k[0] for k in found} == {"Direct", "Regular"}:
            break
    order = {("Direct", "TER"): 0, ("Regular", "TER"): 1, ("Direct", "BER"): 2, ("Regular", "BER"): 3}
    return [found[k] for k in sorted(found, key=order.get)]


def extract_ter_from_text(pages: list, footnotes: list[str] | None = None) -> list[TEREntry]:
    """Fallback for a scheme segment on a page shared with another scheme, where layout text
    interleaves both. Reads default text under an "Expense Ratio" heading: either "Label: x%"
    pairs, or the labels-then-values run default extraction produces for a two-column box
    (Kotak: "Expense Ratio**\nRegular Plan:\nDirect Plan:\n1.43%\n0.61%"). Always typed
    from the same heading/caption rule as the layout path."""
    out: list[TEREntry] = []
    for page in pages:
        lines = page.text.splitlines()
        for i, line in enumerate(lines):
            if not re.search(r"expense\s+ratio", line, re.IGNORECASE):
                continue
            block = lines[i:i + 12]
            marker = _HEADING_MARKER_RE.search(line)
            rtype = "BER" if (
                any(_BASE_RATIO_RE.search(b) for b in block)
                or (marker and _footnote_says_base(marker.group(1), footnotes or []))
            ) else "TER"
            text = " ".join(block)
            pairs = [(m.group(1), m.group(2)) for m in _PLAN_COLON_RE.finditer(text)]
            if len(pairs) < 2:
                labels = [re.match(r"^\s*(direct|regular|other)\b", b, re.IGNORECASE)
                          for b in block[1:]]
                labels = [m.group(1) for m in labels if m]
                pcts = _PCT_RE.findall(" ".join(b for b in block[1:] if not re.match(
                    r"^\s*(direct|regular|other)\b", b, re.IGNORECASE)))
                pairs = list(zip(labels, pcts)) if len(labels) == 2 and len(pcts) >= 2 else []
            seen = set()
            for label, value in pairs:
                plan = _plan(label)
                if plan not in seen and 0 < float(value) < 5:
                    seen.add(plan)
                    out.append(TEREntry(plan_type=plan, ter_pct=float(value),
                                        raw_text=" ".join(text.split())[:200],
                                        confidence="low", ratio_type=rtype))
            if len(seen) == 2:
                return out
            out = []
    return out


_INCEPTION_RE = re.compile(
    r"(?:date\s+of\s+)?(?:inception|allotment)(?:[\s/]+allotment)?(?:\s+date)?[\s/:\-]*"
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s*\d{4}"
    r"|\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ,]+\d{2}(?:\d{2})?\b"
    r"|\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)


def extract_inception_date(pages: list, layout: dict[int, str] | None = None) -> str | None:
    """Scheme inception/allotment date printed on its factsheet page, ISO if parseable. Used
    to resolve "Managing Since: Since Inception". Layout text is tried first: in default
    extraction PPFAS's "Date of Inception / Allotment" label and its date land in different
    columns (found for 2 of 7 schemes in the Aug 2026 factsheet; layout mode finds all 7)."""
    from indian_mf_mcp.ingest.manager_extract import _parse_since_date
    texts = [" ".join(t.split()) for _, t in sorted((layout or {}).items())]
    texts += [p.text for p in pages]
    for text in texts:
        m = _INCEPTION_RE.search(text)
        if m:
            d = _parse_since_date(m.group(1))
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                return d
    return None


# ---------------------------------------------------------------------------
# Factsheet date extraction (from filename or document header)
# ---------------------------------------------------------------------------

_MONTH_YEAR_RE = re.compile(
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*[_\- ]?\s*\d{4}",
    re.IGNORECASE,
)


def _factsheet_date_from_url(url: str) -> str | None:
    """Best-effort ISO date from URL filename. Returns YYYY-MM-01 (month-start)."""
    fname = url.rstrip("/").split("/")[-1].split("?")[0]
    m = _MONTH_YEAR_RE.search(fname)
    if m:
        try:
            raw = re.sub(r"[_\- ]+", " ", m.group(0)).strip()
            dt = datetime.strptime(raw, "%b %Y")
            return dt.date().replace(day=1).isoformat()
        except ValueError:
            pass
    # Try explicit date patterns YYYY-MM-DD or DD-MM-YYYY in the filename
    d_m = re.search(r"(\d{4})-(\d{2})-\d{2}", fname)
    if d_m:
        return f"{d_m.group(1)}-{d_m.group(2)}-01"
    return None


# ---------------------------------------------------------------------------
# Scheme page scoping
# ---------------------------------------------------------------------------

# Captions that sit next to a scheme's title on its OWN factsheet page, and not next to the
# name where it merely appears in an index, annexure, IDCW-history, riskometer or
# "other funds managed" table:
#   PPFAS "Parag Parikh Flexi Cap Fund / Type of Scheme: …"
#   SBI   "Type of Scheme: An open-ended Dynamic Equity Scheme … / SBI FLEXICAP FUND"
#   Nippon "Nippon India Flexi Cap Fund / Flexi Cap Fund / Details as on July 31, 2026"
#   HDFC  "HDFC Flexi Cap Fund / An open ended dynamic equity scheme … / CATEGORY OF SCHEME"
#   Kotak "KOTAK FLEXI CAP FUND / (ERSTWHILE KNOWN AS KOTAK FLEXICAP FUND) / Flexicap fund - An
#          open-ended dynamic equity scheme …"
#   HDFC  "HDFC Aggressive Hybrid Fund / [(Erstwhile HDFC Hybrid Equity Fund) … An" /
#          "open ended hybrid scheme …"  (renamed schemes; the caption wraps mid-phrase)
_TITLE_CAPTION_RE = re.compile(
    r"type\s+of\s+scheme|details\s+as\s+on|category\s+of\s+scheme|\berstwhile\b"
    r"|\bopen[\s-]*ended\b", re.IGNORECASE)
_CAPTION_DISTANCE = 3
# A riskometer section repeats "<scheme> / An open-ended … scheme" for every scheme (PPFAS
# Jul 2026 p.23 "Riskometers as on July 31, 2026"); it is never a scheme's own page.
_RISKOMETER_SECTION_RE = re.compile(r"riskometers?\s+as\s+on|product\s+label(?:l)?ing\s+and\s+riskometer",
                                    re.IGNORECASE)


@dataclass
class SchemeSegment:
    page_number: int
    text: str          # the scheme's own run of lines: its title up to the next scheme title
    sole_title: bool   # the page carries no other scheme's title


_TITLE_WORD_RE = re.compile(r"\b(?:fund|fof|etf)\b", re.IGNORECASE)


def _has_caption(lines: list[str], i: int) -> bool:
    return any(_TITLE_CAPTION_RE.search(near)
               for near in lines[max(0, i - _CAPTION_DISTANCE):i + _CAPTION_DISTANCE + 1])


def scheme_segments(pages: list, scheme_hint: str) -> list[SchemeSegment]:
    """The scheme's own text in a combined factsheet, as (page, segment) pieces.

    A page counts only if a line IS the scheme's name with a scheme-page caption beside it
    (see _TITLE_CAPTION_RE). Within that page, the scheme's text runs from its title to the
    next scheme title, because some pages carry more than one: Kotak's Jul 2026 p.40 is the
    Aggressive Hybrid Fund's page with a "KOTAK LARGE CAP FUND (ERSTWHILE …)" panel below it,
    and reading the whole page attributed the hybrid fund's managers to Large Cap."""
    target = normalize_scheme_name(scheme_hint)
    if not target:
        return []
    brand = normalize_scheme_name(scheme_hint.split()[0])
    out: list[SchemeSegment] = []
    for page in pages:
        lines = [ln for ln in page.text.splitlines() if ln.strip()]
        if any(_RISKOMETER_SECTION_RE.search(ln) for ln in lines[:3]):
            continue
        # Another scheme's title carries the fund house's name, as this one does ("KOTAK LARGE
        # CAP FUND"); a bare category label under the title ("CATEGORY OF SCHEME / FLEXI CAP
        # FUND" on HDFC's page, "Flexi Cap Fund" on Nippon's) is not a scheme and must not
        # cut this scheme's segment short.
        titles = [i for i, ln in enumerate(lines)
                  if len(ln) <= 100 and _TITLE_WORD_RE.search(ln) and _has_caption(lines, i)
                  and normalize_scheme_name(ln).startswith(brand)]
        for i in titles:
            if normalize_scheme_name(lines[i]) != target:
                continue
            nxt = next((j for j in titles if j > i
                        and normalize_scheme_name(lines[j]) != target), len(lines))
            others = {normalize_scheme_name(lines[j]) for j in titles} - {target}
            # Only narrow when another scheme shares the page: default extraction can put a
            # scheme's own sidebar (managers, ratios) BEFORE its title line (ICICI US Bluechip,
            # Aug 2026), so a title-onward cut would lose it on an unshared page.
            text = "\n".join(lines[i:nxt]) if others else page.text
            out.append(SchemeSegment(page.page_number, text, not others))
            break
    return out


def scheme_page_numbers(pages: list, scheme_hint: str) -> list[int]:
    """Pages of a COMBINED factsheet that belong to one scheme: those with a line that IS the
    scheme's name (spacing/punctuation-insensitive) and a scheme-page caption within three
    lines of it. Position on the page is not used — Nippon's and SBI's text starts with a
    sidebar, so the title sits mid-page — and a bare mention is not enough: an annexure,
    index or "performance of other funds managed" table names many schemes, and reading
    managers or expense ratios off it would attribute another fund's figures to this one
    (HDFC Aug 2026 names Flexi Cap on 11 pages; only p.7 is its own). No match means no
    pages: never "the whole document"."""
    return [seg.page_number for seg in scheme_segments(pages, scheme_hint)]


# ---------------------------------------------------------------------------
# Main ingest function
# ---------------------------------------------------------------------------

def _same_person(a: str, b: str) -> bool:
    """A one- or two-letter spelling fix: the first or last name unchanged and the full name
    near-identical ("Ranjhana Gupta"/"Ranjana Gupta"; not "Amit Ganatra"/"Amit Sinha")."""
    from difflib import SequenceMatcher
    wa, wb = a.lower().split(), b.lower().split()
    return ((wa[0] == wb[0] or wa[-1] == wb[-1])
            and SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.9)


def _ratios_by_type(entries: list[TEREntry]) -> dict:
    """{"TER": {"Direct": 1.27, "Regular": 2.06}, "BER": {...}} — only the types found."""
    out: dict = {}
    for e in entries:
        out.setdefault(e.ratio_type, {})[e.plan_type] = e.ter_pct
    return out


def _previous_extract(conn: sqlite3.Connection, scheme_id: str, doc_date: str):
    return conn.execute(
        """SELECT * FROM factsheet_extract WHERE scheme_id = ? AND doc_date < ?
           ORDER BY doc_date DESC LIMIT 1""",
        (scheme_id, doc_date),
    ).fetchone()


def _is_latest(conn: sqlite3.Connection, scheme_id: str, doc_date: str) -> bool:
    row = conn.execute("SELECT MAX(doc_date) FROM factsheet_extract WHERE scheme_id = ?",
                       (scheme_id,)).fetchone()
    return row[0] is None or doc_date >= row[0]


def ingest_factsheet(
    conn: sqlite3.Connection,
    raw: bytes,
    url: str,
    scheme_id: str,
    doc_date: str | None = None,
    scheme_hint: str | None = None,
    whole_document: bool = False,
    parsed: PdfParseResult | None = None,
) -> dict:
    """Process one factsheet for one scheme: archive the blob, parse the PDF (or reuse
    `parsed`), attribute pages to the scheme, extract managers + TER from those pages, record
    what was read in `factsheet_extract`, and diff against the scheme's previous factsheet to
    detect manager/TER changes.

    Page attribution:
      - `scheme_hint` given (the normal, adapter-driven path): a combined factsheet is scoped
        to the pages titled with the scheme (scheme_page_numbers). If none are, nothing is
        extracted — the document is still archived — rather than reading another fund's page.
      - `whole_document=True`: the adapter fetched a per-scheme factsheet; every page is the
        scheme's, provided at least one page names it.
      - neither (manual `mf-mcp ingest-factsheet --url`): every page, as before; the caller
        vouches that the PDF is this scheme's.

    Returns a stats dict: {doc_id, pages, managers_found, ter_entries, change_events, warnings}
    """
    warnings: list[str] = []
    stats: dict = {
        "doc_id": None,
        "pages": [],
        "managers_found": 0,
        "ter_entries": 0,
        "change_events": 0,
        "warnings": warnings,
    }

    if sniff(raw) != FormatKind.PDF:
        warnings.append(f"factsheet at {url} is not a PDF (magic-byte check failed); skipped.")
        return stats

    sha256, _ = blobstore.put(raw)
    doc_id = f"doc-{sha256[:16]}"
    retrieved_at = datetime.now(timezone.utc).isoformat()
    inferred_date = doc_date or _factsheet_date_from_url(url)

    pdf_result = parsed or parse_pdf(raw)
    if parsed is None:
        warnings.extend(pdf_result.warnings)

    known = conn.execute("SELECT 1 FROM document WHERE doc_id = ?", (doc_id,)).fetchone()
    if known is None:
        conn.execute(
            """INSERT INTO document (doc_id, scheme_id, doc_type, doc_date, source_url,
                 sha256, content_type, blob_path, retrieved_at, page_count,
                 parse_status, parse_confidence)
               VALUES (?, ?, 'FACTSHEET', ?, ?, ?, 'pdf', ?, ?, ?, ?, ?)
               ON CONFLICT(sha256) DO NOTHING""",
            (doc_id, scheme_id, inferred_date, url, sha256,
             str(blobstore.blob_path_for(sha256)), retrieved_at,
             pdf_result.page_count, classify_parse_status(pdf_result),
             pdf_result.parse_confidence),
        )
        # A combined factsheet is ingested once per scheme; its pages are stored once.
        conn.executemany(
            "INSERT INTO document_section (doc_id, page_number, headings_json, text) VALUES (?, ?, ?, ?)",
            [(doc_id, p.page_number, json.dumps(p.headings), p.text) for p in pdf_result.pages],
        )
    else:
        conn.execute("UPDATE document SET retrieved_at = ? WHERE doc_id = ?", (retrieved_at, doc_id))
    stats["doc_id"] = doc_id

    if not pdf_result.pages:
        warnings.append("no text extracted from PDF; manager/TER extraction skipped.")
        return stats

    if scheme_hint and not whole_document:
        page_nums = scheme_page_numbers(pdf_result.pages, scheme_hint)
        if not page_nums:
            warnings.append(f"no page of this factsheet is titled {scheme_hint!r}; nothing "
                            "extracted for this scheme (not guessed from other pages).")
            return stats
    elif whole_document and scheme_hint:
        target = normalize_scheme_name(scheme_hint)
        if not any(target in normalize_scheme_name(p.text) for p in pdf_result.pages):
            warnings.append(f"per-scheme factsheet never names {scheme_hint!r}; skipped.")
            return stats
        page_nums = [p.page_number for p in pdf_result.pages]
    else:
        page_nums = [p.page_number for p in pdf_result.pages]
    stats["pages"] = page_nums
    pages = [p for p in pdf_result.pages if p.page_number in page_nums]
    layout_pages = page_nums
    if scheme_hint and not whole_document:
        # Read the scheme's own segment of each page, not the whole page; and only trust the
        # page's layout text (which interleaves columns) when no other scheme shares the page.
        segs = scheme_segments(pdf_result.pages, scheme_hint)
        pages = [PdfPage(page_number=sg.page_number, text=sg.text, headings=[]) for sg in segs]
        layout_pages = [sg.page_number for sg in segs if sg.sole_title]
    layout = layout_texts(raw, layout_pages)

    managers = extract_managers_from_pages(pages)
    inception = extract_inception_date(pages, layout)
    for em in managers:
        if em.managing_since == SINCE_INCEPTION:
            em.managing_since = inception  # None when the factsheet didn't print it
    if inferred_date:
        # A manager printed with "(Effective till <date>)" before this factsheet's date has left.
        managers = [m for m in managers
                    if not (m.managing_until and m.managing_until[:1].isdigit()
                            and m.managing_until < inferred_date)]
        # An incoming manager announced "w.e.f." a date after the factsheet's own date (HDFC,
        # Aug 2026: "Mr. Gopal Agrawal w.e.f. September 01, 2026") is not managing yet.
        managers = [m for m in managers
                    if not (m.managing_since and re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.managing_since)
                            and m.managing_since > inferred_date)]
    # Footnotes belong to the whole page (Kotak's "**… Base Expense Ratio (BER)" note can sit
    # outside the scheme's segment), so they are read from the full page text.
    footnotes = [ln for p in pdf_result.pages if p.page_number in page_nums
                 for ln in p.text.splitlines()]
    ter_entries = extract_ter_from_layout(layout, footnotes)
    if not ter_entries and len(layout_pages) < len(pages):
        ter_entries = extract_ter_from_text(pages, footnotes)
    # Layout text can garble a rotated/overlapping caption (HDFC Focused Fund, Aug 2026: the
    # "Base expense Ratio Including statutory levies…" caption comes out as "exp B en as s e
    # e s e p xp…"); default text keeps it whole. Figures not typed by explicit TER/BER
    # columns are BER when the default text under the scheme's "Expense Ratio" heading says so.
    if any(_BASE_NEAR_HEADING_RE.search(p.text) for p in pages):
        for te in ter_entries:
            if not te.typed_by_columns:
                te.ratio_type = "BER"
    stats["managers_found"] = len(managers)
    stats["ter_entries"] = len(ter_entries)

    as_of = inferred_date or datetime.now(timezone.utc).date().isoformat()
    previous = _previous_extract(conn, scheme_id, as_of)
    latest = _is_latest(conn, scheme_id, as_of)
    conn.execute(
        """INSERT INTO factsheet_extract (scheme_id, doc_id, doc_date, pages_json,
             managers_json, ter_json, extracted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(scheme_id, doc_date) DO UPDATE SET doc_id=excluded.doc_id,
             pages_json=excluded.pages_json, managers_json=excluded.managers_json,
             ter_json=excluded.ter_json, extracted_at=excluded.extracted_at""",
        (scheme_id, doc_id, as_of, json.dumps(page_nums),
         json.dumps([{"name": m.name, "managing_since": m.managing_since} for m in managers]),
         json.dumps(_ratios_by_type(ter_entries)), retrieved_at),
    )

    # Current-state tables (assignments, TER) and change events follow the newest factsheet
    # only; an older month processed late is recorded above but must not rewind them.
    if not latest:
        warnings.append(f"a newer factsheet is already recorded for {scheme_id}; this "
                        f"{as_of} one was archived and recorded but did not update current state.")
        return stats

    detected_date = datetime.now(timezone.utc).date().isoformat()
    open_rows = {r["name_normalised"]: r for r in mrep.get_current_managers_for_scheme(conn, scheme_id)}
    curr_names = {m.name for m in managers}
    prev_names = ({m["name"] for m in json.loads(previous["managers_json"] or "[]")}
                  if previous is not None else set())

    if managers:
        for em in managers:
            open_row = open_rows.get(em.name)
            if open_row is not None:
                # Already managing: never open a second row. If this factsheet states an
                # earlier start than the one on record (which may have been only "first seen
                # in factsheet X"), correct it in place.
                if em.managing_since and em.managing_since < open_row["from_date"]:
                    conn.execute(
                        """UPDATE manager_assignment SET from_date = ?, notes = NULL
                           WHERE scheme_id = ? AND manager_id = ? AND to_date IS NULL""",
                        (em.managing_since, scheme_id, open_row["manager_id"]),
                    )
                continue
            mgr_id = mrep.upsert_manager(conn, em.name)
            mrep.upsert_manager_assignment(
                conn, scheme_id, mgr_id,
                from_date=em.managing_since or as_of,
                evidence_doc_id=doc_id,
                confidence="observed",
                notes=None if em.managing_since else (
                    "managing-since not stated (or 'since inception' with no inception date "
                    f"printed); from_date is the first factsheet it was observed in ({as_of})"),
            )
        # Departures: open assignments whose manager this factsheet no longer lists. Only
        # when extraction found SOMEONE — an empty read is a parsing miss, not a resignation.
        for name, row in open_rows.items():
            if name not in curr_names:
                mrep.close_assignment(conn, scheme_id, row["manager_id"], to_date=as_of)
        # A change needs a previous month to differ from; the first factsheet ever ingested
        # for a scheme is a baseline, not an event. A manager whose own "managing since" is
        # earlier than the previous factsheet was already managing then — the previous read
        # missed them (a redesigned layout: PPFAS's Jul 2026 edition lists managers in a
        # different table than Aug 2026's), so their appearance is not an appointment.
        prev_date = previous["doc_date"] if previous is not None else None
        already = {m.name for m in managers
                   if prev_date and m.managing_since and m.managing_since[:1].isdigit()
                   and m.managing_since < prev_date}
        added = curr_names - prev_names
        departed = prev_names - curr_names
        # A name whose spelling the AMC corrected is the same person, not an exit + entry
        # (SBI Jul 2026: "Ranjhana Gupta" -> "Ranjana Gupta"). Paired before the "already
        # managing" rule, which would otherwise drop the new spelling and strand the old one
        # as a departure.
        for a in sorted(added):
            twin = next((d for d in departed if _same_person(a, d)), None)
            if twin is not None:
                added.discard(a)
                departed.discard(twin)
        added -= already
        if prev_names and (added or departed):
            mrep.insert_change_event(
                conn, scheme_id, event_type="manager_change",
                detected_date=detected_date,
                effective_date=as_of,
                detected_from="factsheet_diff",
                before={"managers": sorted(prev_names)},
                after={"managers": sorted(curr_names)},
                evidence_doc_id=doc_id,
                confidence="observed",
            )
            stats["change_events"] += 1

    prev_ter = json.loads(previous["ter_json"] or "{}") if previous is not None else {}
    for te in ter_entries:
        if te.ratio_type != "TER":
            continue  # BER is recorded in factsheet_extract only; ter_history is TER-only
        plan_row = conn.execute(
            """SELECT plan_id FROM plan WHERE scheme_id = ? AND plan_type = ?
               AND option_type = 'Growth' LIMIT 1""",
            (scheme_id, te.plan_type),
        ).fetchone()
        if plan_row is None:
            warnings.append(f"no {te.plan_type}/Growth plan found for scheme {scheme_id}; "
                            "TER not persisted.")
            continue
        before = (prev_ter.get("TER") or {}).get(te.plan_type)
        if before is not None and abs(before - te.ter_pct) > 0.001:
            mrep.insert_change_event(
                conn, scheme_id, event_type="ter_change",
                detected_date=detected_date,
                effective_date=as_of,
                detected_from="factsheet_diff",
                before={"ter_pct": before, "plan_type": te.plan_type},
                after={"ter_pct": te.ter_pct, "plan_type": te.plan_type},
                evidence_doc_id=doc_id,
                confidence="observed",
            )
            stats["change_events"] += 1
        mrep.upsert_ter(conn, plan_row["plan_id"], as_of_date=as_of, ter_pct=te.ter_pct,
                        source="factsheet", source_doc_id=doc_id)

    return stats


def backfill_factsheets(
    conn: sqlite3.Connection,
    adapter,
    schemes: list[tuple[str, str]],
    since,
    log=lambda msg: None,
) -> dict:
    """Discover, fetch and ingest factsheets for (scheme_id, scheme_hint) pairs of one AMC.

    Adapters declare `factsheet_scope`:
      - "combined" (one PDF per month covering every scheme): list the months once, fetch and
        parse each PDF once, and attribute pages to every scheme from that single parse —
        not one download + parse per scheme per month.
      - "per_scheme" (the default when undeclared): list/fetch per scheme; every page of a
        scheme's own file is its page.
    Months are processed oldest first so each one is diffed against the month before it.
    """
    totals: dict = {"documents": 0, "extracted": 0, "no_pages": 0, "change_events": 0,
                    "errors": []}

    def _ingest(raw, ref, scheme_id, hint, whole, parsed):
        doc_date = ref.as_of_date.isoformat() if ref.as_of_date else None
        st = ingest_factsheet(conn, raw, ref.url, scheme_id, doc_date=doc_date,
                              scheme_hint=hint, whole_document=whole, parsed=parsed)
        totals["change_events"] += st["change_events"]
        if st["pages"]:
            totals["extracted"] += 1
        else:
            totals["no_pages"] += 1
        return st

    if getattr(adapter, "factsheet_scope", "per_scheme") == "combined":
        from indian_mf_mcp.ingest.amc_adapters.base import DocType
        refs = sorted(adapter.list_documents(DocType.FACTSHEET, since=since),
                      key=lambda r: r.as_of_date or since)
        log(f"{len(refs)} combined factsheet(s) since {since} for {len(schemes)} scheme(s)")
        for ref in refs:
            try:
                raw = adapter.fetch(ref)
            except Exception as exc:  # noqa: BLE001
                totals["errors"].append({"url": ref.url, "error": str(exc)})
                log(f"  {ref.as_of_date}: FETCH FAILED {exc}")
                continue
            totals["documents"] += 1
            parsed = parse_pdf(raw) if sniff(raw) == FormatKind.PDF else None
            hits = 0
            for scheme_id, hint in schemes:
                try:
                    st = _ingest(raw, ref, scheme_id, hint, False, parsed)
                    hits += bool(st["pages"])
                except Exception as exc:  # noqa: BLE001
                    totals["errors"].append({"url": ref.url, "scheme_id": scheme_id,
                                             "error": str(exc)})
            log(f"  {ref.as_of_date}: pages found for {hits}/{len(schemes)} scheme(s)")
        return totals

    from indian_mf_mcp.ingest.amc_adapters.base import DocType
    for scheme_id, hint in schemes:
        try:
            refs = sorted(adapter.list_documents(DocType.FACTSHEET, since=since, scheme_hint=hint),
                          key=lambda r: r.as_of_date or since)
        except Exception as exc:  # noqa: BLE001
            totals["errors"].append({"scheme_id": scheme_id, "error": str(exc)})
            continue
        for ref in refs:
            try:
                raw = adapter.fetch(ref)
                totals["documents"] += 1
                st = _ingest(raw, ref, scheme_id, hint, True, None)
                log(f"  {hint} {ref.as_of_date}: managers={st['managers_found']} "
                    f"ter={st['ter_entries']} events={st['change_events']}")
            except Exception as exc:  # noqa: BLE001
                totals["errors"].append({"url": ref.url, "scheme_id": scheme_id, "error": str(exc)})
    return totals


def ingest_factsheet_from_url(
    conn: sqlite3.Connection,
    url: str,
    scheme_id: str,
    doc_date: str | None = None,
) -> dict:
    """Convenience wrapper: fetch URL, then call ingest_factsheet."""
    from indian_mf_mcp.ingest.amc_adapters.base import http_get
    raw = http_get(url)
    return ingest_factsheet(conn, raw, url, scheme_id, doc_date=doc_date)


def ingest_sid_from_url(
    conn: sqlite3.Connection,
    url: str,
    scheme_id: str,
    doc_date: str | None = None,
) -> str:
    """Fetch + ingest a SID PDF. Returns doc_id.

    SIDs are stored as Document + DocumentSection rows only — no manager/TER extraction
    is attempted here because SIDs lag badly vs factsheets (spec §11). The document is
    made available to get_document() for prose retrieval by Claude.
    """
    from indian_mf_mcp.ingest.document_ingest import ingest_document_from_url
    return ingest_document_from_url(
        conn, url, doc_type="SID", scheme_id=scheme_id, doc_date=doc_date
    )
