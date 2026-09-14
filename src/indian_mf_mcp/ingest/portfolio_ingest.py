"""Orchestrates: adapter.list_documents -> fetch -> blob store (sha256, never evicted) ->
parse -> reconciliation-gated persistence. Shared across all AMC adapters (spec §7.1: only
discovery is AMC-specific)."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Callable

import sqlite3

from indian_mf_mcp.ingest.amc_adapters.base import AMCAdapter, DocType, DocumentRef
from indian_mf_mcp.parsers.sniff import FormatKind, sniff
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xls, parse_portfolio_xlsx
from indian_mf_mcp.store import blobstore
from indian_mf_mcp.store import portfolio_repository as prepo

# Format-specific parser + the content_type/extension recorded on the document row.
_PARSERS = {
    FormatKind.XLSX: (parse_portfolio_xlsx, "xlsx"),
    FormatKind.XLS_BIFF: (parse_portfolio_xls, "xls"),
}


def _snapshot_id(scheme_id: str, as_of_date: str, disclosure_type: str) -> str:
    key = f"{scheme_id}::{as_of_date}::{disclosure_type}"
    return "snap-" + hashlib.sha1(key.encode()).hexdigest()[:16]


def ingest_scheme_portfolios(
    conn: sqlite3.Connection,
    adapter: AMCAdapter,
    scheme_id: str,
    scheme_hint: str,
    since: date,
    sheet_resolver: Callable[[bytes, str], str | None] | None = None,
) -> dict:
    """sheet_resolver: for AMCs that publish one combined workbook per month covering every
    scheme (e.g. SBI), a callable (raw_bytes, scheme_hint) -> sheet_name to select. None means
    one file per scheme (PPFAS-style; uses the workbook's first/only sheet)."""
    refs = adapter.list_documents(DocType.MONTHLY_PORTFOLIO, since=since, scheme_hint=scheme_hint)
    stats = {"documents_found": len(refs), "ingested": 0, "skipped_format": 0,
              "skipped_reconciliation_failed": 0, "skipped_sheet_not_found": 0,
              "skipped_ambiguous_multi_sheet": 0, "errors": []}

    for ref in refs:
        try:
            raw = adapter.fetch(ref)
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append({"url": ref.url, "error": str(exc)})
            continue

        fmt = sniff(raw)
        parser_entry = _PARSERS.get(fmt)
        if parser_entry is None:
            # HTML-masquerading-as-xls and anything else unrecognised: never guess the
            # content from the extension, never attempt XLSX/XLS parsing on it.
            stats["skipped_format"] += 1
            continue
        parse_fn, content_type = parser_entry

        sheet_name = None
        if sheet_resolver is not None:
            sheet_name = sheet_resolver(raw, scheme_hint)
            if sheet_name is None:
                stats["skipped_sheet_not_found"] += 1
                continue

        sha256, _ = blobstore.put(raw)
        doc_id = f"doc-{sha256[:16]}"
        retrieved_at = datetime.now(timezone.utc).isoformat()

        result = parse_fn(raw, sheet_name=sheet_name)
        as_of_str = ref.as_of_date.isoformat() if ref.as_of_date else result.as_of_date_str

        # A one-file-per-scheme adapter (sheet_resolver=None) receiving a workbook with more
        # than one sheet is a structural mismatch, not the normal case it was configured for —
        # e.g. some AMCs' older archives quietly switch to one combined multi-scheme workbook
        # per month. Silently parsing "the first sheet" here could attribute a different
        # scheme's holdings to this scheme_id. Flag it rather than guess: the raw bytes are
        # still archived below, but no snapshot is persisted from an unverified sheet pick.
        ambiguous_multi_sheet = sheet_resolver is None and result.sheet_count > 1

        conn.execute(
            """INSERT INTO document (doc_id, scheme_id, doc_type, doc_date, source_url, sha256,
                 content_type, blob_path, retrieved_at, parse_status, parse_confidence)
               VALUES (?, ?, 'PORTFOLIO', ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sha256) DO NOTHING""",
            (doc_id, scheme_id, as_of_str, ref.url, sha256, content_type,
             str(blobstore.blob_path_for(sha256)), retrieved_at,
             "parsed" if result.holdings else "unparseable", result.parse_confidence),
        )

        if ambiguous_multi_sheet:
            stats["skipped_ambiguous_multi_sheet"] += 1
            continue

        if not result.reconciliation_ok:
            stats["skipped_reconciliation_failed"] += 1
            # Still record the document (raw bytes preserved forever) but do not persist a
            # snapshot Claude could mistake for a clean one.
            continue

        snapshot_id = _snapshot_id(scheme_id, as_of_str, "monthly")
        prepo.upsert_snapshot(conn, snapshot_id, scheme_id, as_of_str, "monthly",
                               doc_id, result, retrieved_at)
        stats["ingested"] += 1

    return stats
