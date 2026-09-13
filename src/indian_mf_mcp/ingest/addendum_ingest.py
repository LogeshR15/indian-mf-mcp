"""Addendum ingestion pipeline (spec §14, §4, §11).

Addenda are SEBI-mandated legal notices that AMCs must issue when making material changes
(manager changes, benchmark changes, TER revisions, mandate changes, etc.).

Key difference from factsheet_ingest.py:
  - ChangeEvents produced here get confidence='official' (legal source, not observation)
  - The raw document is stored in the blob store (never evicted — source docs are permanent)
  - Same idempotency mechanism: sha256 check prevents re-processing the same file

Pipeline:
  1. Receive raw bytes (already fetched by CLI or adapter)
  2. sha256 check → if seen, return cached result
  3. Store in blob store (content-addressed)
  4. Insert document record (doc_type='addendum')
  5. Parse PDF text (pypdf / pdfplumber via parsers.pdf_text)
  6. Extract change signals via addendum_signals.extract_change_signals()
  7. Write ChangeEvents to change_event table with confidence='official'
  8. Return stats dict
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from indian_mf_mcp.ingest.addendum_signals import ChangeSignal, extract_change_signals
from indian_mf_mcp.parsers.pdf_text import extract_pages
from indian_mf_mcp.parsers.sniff import sniff_format
from indian_mf_mcp.store import blobstore

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_doc_date(url: str) -> Optional[str]:
    """Try to extract a YYYY-MM-DD date from the URL string."""
    import re
    # Match YYYY-MM-DD or YYYYMMDD
    m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", url)
    if m:
        try:
            from datetime import date
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d.isoformat()
        except ValueError:
            pass
    return None


def ingest_addendum(
    conn: sqlite3.Connection,
    raw: bytes,
    url: str,
    scheme_id: str,
    doc_date: Optional[str] = None,
) -> dict:
    """Ingest a raw addendum PDF.

    Args:
        conn: SQLite connection (open, caller controls commit lifecycle)
        raw: raw bytes of the addendum PDF
        url: canonical URL of the document (for provenance)
        scheme_id: scheme this addendum applies to
        doc_date: ISO date of the document (inferred from URL if None)

    Returns:
        stats dict: {doc_id, pages, change_events, skipped, warnings}
    """
    sha256 = hashlib.sha256(raw).hexdigest()
    doc_date = doc_date or _derive_doc_date(url) or datetime.now(timezone.utc).date().isoformat()

    # Idempotency: skip if we've already seen this exact file
    existing = conn.execute(
        "SELECT doc_id FROM document WHERE sha256 = ?", (sha256,)
    ).fetchone()
    if existing:
        log.debug("Addendum already ingested: sha256=%s doc_id=%s", sha256, existing["doc_id"])
        return {
            "doc_id": existing["doc_id"],
            "pages": 0,
            "change_events": 0,
            "skipped": True,
            "warnings": [],
        }

    warnings: list[str] = []

    # Store raw bytes in content-addressed blob store
    blob_path = blobstore.store(raw, sha256)

    # Detect format and parse
    fmt = sniff_format(raw)
    pages: list[str] = []
    parse_status = "parsed"
    parse_confidence = 1.0

    if fmt in ("pdf",):
        try:
            pages = extract_pages(raw)
        except Exception as exc:
            warnings.append(f"PDF parse error: {exc}")
            parse_status = "parse_error"
            parse_confidence = 0.0
    elif fmt in ("html", "unknown"):
        # Some addenda are HTML notices — extract text naively
        try:
            from selectolax.parser import HTMLParser
            tree = HTMLParser(raw.decode("utf-8", errors="replace"))
            text = tree.root.text(separator="\n")
            pages = [text]
        except Exception as exc:
            warnings.append(f"HTML parse error: {exc}")
            parse_status = "parse_error"
            parse_confidence = 0.0
    else:
        warnings.append(f"Unsupported format for addendum: {fmt}")
        parse_status = f"unsupported_format_{fmt}"
        parse_confidence = 0.0

    # Insert document record
    doc_id = f"add_{scheme_id}_{doc_date}_{sha256[:8]}"
    now = _now_iso()
    conn.execute(
        """INSERT OR REPLACE INTO document
           (doc_id, scheme_id, amc_id, doc_type, doc_date, source_url, sha256,
            content_type, blob_path, retrieved_at, page_count, parse_status, parse_confidence)
           VALUES (?,?,NULL,'addendum',?,?,?,'application/pdf',?,?,?,?,?)""",
        (doc_id, scheme_id, doc_date, url, sha256, blob_path, now,
         len(pages), parse_status, parse_confidence),
    )

    # Store document sections
    for i, page_text in enumerate(pages, start=1):
        conn.execute(
            """INSERT INTO document_section (doc_id, page_number, headings_json, text)
               VALUES (?, ?, '[]', ?)""",
            (doc_id, i, page_text),
        )

    # Extract change signals
    signals: list[ChangeSignal] = []
    if pages and parse_status == "parsed":
        try:
            signals = extract_change_signals(pages, doc_date)
        except Exception as exc:
            warnings.append(f"Signal extraction error: {exc}")

    # Write ChangeEvents
    event_count = 0
    for sig in signals:
        event_id = str(uuid.uuid4())
        conn.execute(
            """INSERT OR IGNORE INTO change_event
               (event_id, scheme_id, event_type, effective_date, detected_date,
                detected_from, before_json, after_json, evidence_doc_id, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                scheme_id,
                sig.event_type,
                sig.effective_date,
                now[:10],  # detected_date = today
                "addendum",
                json.dumps({"value": sig.before, "sub_type": sig.sub_type,
                             "raw_excerpt": sig.raw_excerpt[:200]}) if sig.before or sig.sub_type else None,
                json.dumps({"value": sig.after, "sub_type": sig.sub_type,
                             "raw_excerpt": sig.raw_excerpt[:200]}) if sig.after or sig.sub_type else None,
                doc_id,
                sig.confidence,  # always 'official'
            ),
        )
        event_count += 1
        log.info(
            "ChangeEvent [official]: scheme=%s type=%s eff=%s",
            scheme_id, sig.event_type, sig.effective_date,
        )

    conn.commit()

    return {
        "doc_id": doc_id,
        "pages": len(pages),
        "change_events": event_count,
        "skipped": False,
        "signals": [
            {
                "event_type": s.event_type,
                "effective_date": s.effective_date,
                "before": s.before,
                "after": s.after,
                "sub_type": s.sub_type,
                "confidence": s.confidence,
            }
            for s in signals
        ],
        "warnings": warnings,
    }


def ingest_addendum_from_url(
    conn: sqlite3.Connection,
    url: str,
    scheme_id: str,
    doc_date: Optional[str] = None,
) -> dict:
    """Fetch and ingest an addendum from a URL.

    Idempotent: if the sha256 already exists in the store, returns cached stats.
    """
    import httpx
    from indian_mf_mcp import config

    log.info("Fetching addendum: %s", url)
    with httpx.Client() as client:
        resp = client.get(
            url,
            headers={"User-Agent": config.USER_AGENT},
            follow_redirects=True,
            timeout=60,
        )
        resp.raise_for_status()
    raw = resp.content

    return ingest_addendum(conn, raw, url, scheme_id, doc_date=doc_date)
