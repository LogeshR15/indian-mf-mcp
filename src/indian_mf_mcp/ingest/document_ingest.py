"""Generic document ingestion: fetch -> blob store (sha256, never evicted) -> sniff -> parse
-> persist Document + DocumentSection rows. Shared across SID/KIM/factsheet/addendum/annual
report — whatever the doc_type, the storage and section-extraction shape is the same.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from indian_mf_mcp.ingest.amc_adapters.base import http_get
from indian_mf_mcp.parsers.pdf_text import parse_pdf
from indian_mf_mcp.parsers.sniff import FormatKind, sniff
from indian_mf_mcp.store import blobstore


def ingest_document_from_url(
    conn: sqlite3.Connection,
    url: str,
    doc_type: str,
    scheme_id: str | None = None,
    amc_id: str | None = None,
    doc_date: str | None = None,
) -> str:
    """Fetch a document by URL, store it content-addressed, parse if it's a PDF, persist.
    Returns doc_id. Idempotent on content hash — re-ingesting the same bytes is a no-op write.
    """
    raw = http_get(url)
    sha256, _ = blobstore.put(raw)
    doc_id = f"doc-{sha256[:16]}"
    retrieved_at = datetime.now(timezone.utc).isoformat()
    fmt = sniff(raw)

    page_count = None
    parse_status = "unparsed"
    parse_confidence = 0.0

    result = None
    if fmt == FormatKind.PDF:
        result = parse_pdf(raw)
        page_count = result.page_count
        parse_confidence = result.parse_confidence
        parse_status = "parsed" if result.pages else "unparseable"
    else:
        parse_status = f"unsupported_format:{fmt.value}"

    conn.execute(
        """INSERT INTO document (doc_id, scheme_id, amc_id, doc_type, doc_date, source_url,
             sha256, content_type, blob_path, retrieved_at, page_count, parse_status, parse_confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(sha256) DO UPDATE SET
             retrieved_at=excluded.retrieved_at""",
        (doc_id, scheme_id, amc_id, doc_type, doc_date, url, sha256,
         fmt.value, str(blobstore.blob_path_for(sha256)), retrieved_at,
         page_count, parse_status, parse_confidence),
    )

    if result is not None:
        conn.execute("DELETE FROM document_section WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            "INSERT INTO document_section (doc_id, page_number, headings_json, text) VALUES (?, ?, ?, ?)",
            [(doc_id, p.page_number, json.dumps(p.headings), p.text) for p in result.pages],
        )
    return doc_id
