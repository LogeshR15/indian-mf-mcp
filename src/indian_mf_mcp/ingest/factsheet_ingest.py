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
from indian_mf_mcp.ingest.manager_extract import ExtractedManager, extract_managers_from_pages
from indian_mf_mcp.parsers.pdf_text import classify_parse_status, parse_pdf
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
# Main ingest function
# ---------------------------------------------------------------------------

def ingest_factsheet(
    conn: sqlite3.Connection,
    raw: bytes,
    url: str,
    scheme_id: str,
    doc_date: str | None = None,
) -> dict:
    """Process a factsheet's raw bytes: store blob, parse PDF, extract managers + TER,
    diff against the previous factsheet for this scheme to detect ChangeEvents.

    Returns a stats dict: {doc_id, managers_found, ter_entries, change_events, warnings}
    """
    warnings: list[str] = []
    stats: dict = {
        "doc_id": None,
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

    pdf_result = parse_pdf(raw)
    warnings.extend(pdf_result.warnings)

    parse_status = classify_parse_status(pdf_result)
    conn.execute(
        """INSERT INTO document (doc_id, scheme_id, doc_type, doc_date, source_url,
             sha256, content_type, blob_path, retrieved_at, page_count,
             parse_status, parse_confidence)
           VALUES (?, ?, 'FACTSHEET', ?, ?, ?, 'pdf', ?, ?, ?, ?, ?)
           ON CONFLICT(sha256) DO UPDATE SET retrieved_at=excluded.retrieved_at""",
        (doc_id, scheme_id, inferred_date, url, sha256,
         str(blobstore.blob_path_for(sha256)), retrieved_at,
         pdf_result.page_count, parse_status, pdf_result.parse_confidence),
    )
    conn.executemany(
        "INSERT INTO document_section (doc_id, page_number, headings_json, text) VALUES (?, ?, ?, ?)",
        [(doc_id, p.page_number, json.dumps(p.headings), p.text) for p in pdf_result.pages],
    )
    stats["doc_id"] = doc_id

    if not pdf_result.pages:
        warnings.append("no text extracted from PDF; manager/TER extraction skipped.")
        return stats

    # ---- Manager extraction ----
    managers = extract_managers_from_pages(pdf_result.pages)
    stats["managers_found"] = len(managers)

    detected_date = datetime.now(timezone.utc).date().isoformat()

    # Get the managers we previously had for this scheme
    prev_managers = mrep.get_current_managers_for_scheme(conn, scheme_id)
    prev_names = {r["name_normalised"] for r in prev_managers}
    curr_names = {m.name for m in managers}

    for em in managers:
        mgr_id = mrep.upsert_manager(conn, em.name)
        mrep.upsert_manager_assignment(
            conn, scheme_id, mgr_id,
            from_date=em.managing_since or inferred_date or detected_date,
            evidence_doc_id=doc_id,
            confidence="observed",
        )

    # Detect manager additions (new manager not seen before)
    for name in curr_names - prev_names:
        mrep.insert_change_event(
            conn, scheme_id, event_type="manager_change",
            detected_date=detected_date,
            effective_date=inferred_date,
            detected_from="factsheet_diff",
            before={"managers": sorted(prev_names)},
            after={"managers": sorted(curr_names)},
            evidence_doc_id=doc_id,
            confidence="observed",
        )
        stats["change_events"] += 1
        break  # one event per batch of changes

    # Detect manager departures (old manager no longer in latest factsheet)
    for name in prev_names - curr_names:
        # Close their assignment
        prev_row = next((r for r in prev_managers if r["name_normalised"] == name), None)
        if prev_row:
            mrep.close_assignment(conn, scheme_id, prev_row["manager_id"],
                                  to_date=inferred_date or detected_date)
        if not (curr_names - prev_names):  # only emit one combined event if we haven't yet
            mrep.insert_change_event(
                conn, scheme_id, event_type="manager_change",
                detected_date=detected_date,
                effective_date=inferred_date,
                detected_from="factsheet_diff",
                before={"managers": sorted(prev_names)},
                after={"managers": sorted(curr_names)},
                evidence_doc_id=doc_id,
                confidence="observed",
            )
            stats["change_events"] += 1
            break

    # ---- TER extraction ----
    ter_entries = extract_ter_from_pages(pdf_result.pages)
    stats["ter_entries"] = len(ter_entries)

    for te in ter_entries:
        # Match the plan type to an actual plan_id for this scheme
        plan_row = conn.execute(
            """SELECT plan_id FROM plan WHERE scheme_id = ? AND plan_type = ?
               AND option_type = 'Growth' LIMIT 1""",
            (scheme_id, te.plan_type),
        ).fetchone()
        if plan_row is None:
            warnings.append(
                f"no {te.plan_type}/Growth plan found for scheme {scheme_id}; "
                "TER not persisted."
            )
            continue

        # Check if TER changed vs the previous value
        prev_ter = mrep.get_latest_ter(conn, plan_row["plan_id"])
        if prev_ter and abs(prev_ter["ter_pct"] - te.ter_pct) > 0.001:
            mrep.insert_change_event(
                conn, scheme_id, event_type="ter_change",
                detected_date=detected_date,
                effective_date=inferred_date,
                detected_from="factsheet_diff",
                before={"ter_pct": prev_ter["ter_pct"], "plan_type": te.plan_type},
                after={"ter_pct": te.ter_pct, "plan_type": te.plan_type},
                evidence_doc_id=doc_id,
                confidence="observed",
            )
            stats["change_events"] += 1

        mrep.upsert_ter(
            conn, plan_row["plan_id"],
            as_of_date=inferred_date or detected_date,
            ter_pct=te.ter_pct,
            source="factsheet",
            source_doc_id=doc_id,
        )

    return stats


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
