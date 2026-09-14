"""Regression tests for ingest/addendum_ingest.py — the full addendum ingest pipeline.

ingest_addendum() previously imported `parsers.pdf_text.extract_pages` and
`parsers.sniff.sniff_format`, neither of which exist (the real functions are `parse_pdf`
and `sniff`), and called the nonexistent `blobstore.store(raw, sha256)` instead of
`blobstore.put(raw)`. The module failed to import at all, so `mf-mcp ingest-addendum`,
`backfill-addenda`, and the addendum branch of `mf-mcp backfill` crashed immediately —
the only `official`-confidence ChangeEvent pathway was entirely dead code. These tests
exercise the real pipeline end to end (HTML- and PDF-sniffed addenda) so this class of
bug can't ship silently again.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from indian_mf_mcp.ingest.addendum_ingest import ingest_addendum


def _in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        conn.executescript(f.read())
    return conn


def _html_addendum(body: str) -> bytes:
    return f"<html><body><p>{body}</p></body></html>".encode("utf-8")


@pytest.fixture(autouse=True)
def _isolated_blob_dir(tmp_path, monkeypatch):
    from indian_mf_mcp import config
    monkeypatch.setattr(config, "BLOB_DIR", tmp_path)


class TestIngestAddendumHtml:
    """Some AMCs publish addenda as HTML notices rather than PDFs — sniff() routes these
    through the selectolax text-extraction branch."""

    def test_manager_appointment_produces_official_change_event(self):
        conn = _in_memory_db()
        raw = _html_addendum(
            "Mr. Rajeev Sharma has been appointed as fund manager "
            "w.e.f. 1st September 2026."
        )
        result = ingest_addendum(
            conn, raw, url="https://example-amc.com/addendum-1.html",
            scheme_id="TEST_SCHEME", doc_date="2026-09-01",
        )

        assert result["skipped"] is False
        assert result["change_events"] == 1
        assert result["signals"][0]["event_type"] == "manager_change"
        assert result["signals"][0]["confidence"] == "official"
        assert result["signals"][0]["effective_date"] == "2026-09-01"

        # The document row must exist and its blob must actually have been written to
        # disk via blobstore.put — this is exactly the call that used to raise
        # AttributeError('store').
        doc = conn.execute(
            "SELECT * FROM document WHERE doc_id = ?", (result["doc_id"],)
        ).fetchone()
        assert doc is not None
        assert doc["doc_type"] == "addendum"
        assert doc["parse_status"] == "parsed"
        blob_path = Path(doc["blob_path"])
        assert blob_path.exists()
        assert blob_path.read_bytes() == raw

        # The ChangeEvent itself must be persisted with official confidence.
        events = conn.execute(
            "SELECT * FROM change_event WHERE scheme_id = ?", ("TEST_SCHEME",)
        ).fetchall()
        assert len(events) == 1
        assert events[0]["confidence"] == "official"
        assert events[0]["detected_from"] == "addendum"
        assert events[0]["event_type"] == "manager_change"

        # Section text must be retrievable for get_document().
        section = conn.execute(
            "SELECT text FROM document_section WHERE doc_id = ?", (result["doc_id"],)
        ).fetchone()
        assert "Rajeev Sharma" in section["text"]

    def test_text_with_no_specific_signal_still_falls_back_to_a_generic_addendum_event(self):
        """addendum_signals.extract_change_signals() deliberately records a generic
        'addendum' ChangeEvent when a real document produced no field-specific match, so
        the notice still surfaces in list_disclosure_events rather than vanishing
        silently. Confirms the ingest pipeline preserves that fallback, not that it
        invents zero events."""
        conn = _in_memory_db()
        raw = _html_addendum("This notice contains no material change of any kind.")
        result = ingest_addendum(
            conn, raw, url="https://example-amc.com/addendum-2.html",
            scheme_id="TEST_SCHEME", doc_date="2026-09-01",
        )
        assert result["skipped"] is False
        assert result["change_events"] == 1
        assert result["signals"][0]["event_type"] == "addendum"

    def test_idempotent_on_identical_bytes(self):
        conn = _in_memory_db()
        raw = _html_addendum(
            "Mr. Rajeev Sharma has been appointed as fund manager "
            "w.e.f. 1st September 2026."
        )
        first = ingest_addendum(
            conn, raw, url="https://example-amc.com/addendum-1.html",
            scheme_id="TEST_SCHEME", doc_date="2026-09-01",
        )
        second = ingest_addendum(
            conn, raw, url="https://example-amc.com/addendum-1.html",
            scheme_id="TEST_SCHEME", doc_date="2026-09-01",
        )
        assert first["skipped"] is False
        assert second["skipped"] is True
        assert second["doc_id"] == first["doc_id"]
        # Re-ingesting the same file must not duplicate the ChangeEvent.
        events = conn.execute(
            "SELECT COUNT(*) AS n FROM change_event WHERE scheme_id = ?", ("TEST_SCHEME",)
        ).fetchone()
        assert events["n"] == 1


def _minimal_pdf_bytes(text: str) -> bytes:
    """Hand-rolled minimal one-page PDF with a single text-showing content stream —
    avoids pulling in a PDF-generation library just for a test fixture. Verified against
    pypdf.PdfReader (the library parse_pdf() actually uses) to extract `text` back out
    byte-for-byte."""

    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = f"BT /F1 12 Tf 72 720 Td ({esc(text)}) Tj ET"
    stream_body = content.encode("latin-1")

    parts: list[bytes] = [b"%PDF-1.4\n"]
    offsets = [0]

    def add_obj(num: int, body: bytes) -> None:
        offsets.append(sum(len(p) for p in parts))
        parts.append(f"{num} 0 obj\n".encode("latin-1"))
        parts.append(body)
        parts.append(b"\nendobj\n")

    add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_obj(3, b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
               b"/MediaBox [0 0 612 792] /Contents 5 0 R >>")
    add_obj(4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    stream_obj = (f"<< /Length {len(stream_body)} >>\nstream\n".encode("latin-1")
                  + stream_body + b"\nendstream")
    add_obj(5, stream_obj)

    xref_offset = sum(len(p) for p in parts)
    n = 6
    xref_lines = [f"xref\n0 {n}\n".encode("latin-1"), b"0000000000 65535 f \n"]
    for off in offsets[1:]:
        xref_lines.append(f"{off:010d} 00000 n \n".encode("latin-1"))
    trailer = (f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
               .encode("latin-1"))

    parts.extend(xref_lines)
    parts.append(trailer)
    return b"".join(parts)


class TestIngestAddendumPdf:
    """A real, parseable PDF (not just an HTML stand-in) to prove the
    sniff()->FormatKind.PDF->parse_pdf() branch — the one the broken `extract_pages`
    import used to block entirely — actually works end to end."""

    def test_pdf_addendum_is_sniffed_and_parsed(self):
        conn = _in_memory_db()
        raw = _minimal_pdf_bytes("Change in the fund manager w.e.f. 01-09-2026.")
        result = ingest_addendum(
            conn, raw, url="https://example-amc.com/addendum-3.pdf",
            scheme_id="TEST_SCHEME", doc_date="2026-09-01",
        )
        assert result["skipped"] is False
        assert result["pages"] == 1
        assert result["change_events"] == 1
        assert result["signals"][0]["event_type"] == "manager_change"

        doc = conn.execute(
            "SELECT * FROM document WHERE doc_id = ?", (result["doc_id"],)
        ).fetchone()
        assert doc["content_type"] == "application/pdf"
        assert doc["parse_status"] == "parsed"
        blob_path = Path(doc["blob_path"])
        assert blob_path.exists()
        assert blob_path.read_bytes() == raw

        section = conn.execute(
            "SELECT text FROM document_section WHERE doc_id = ?", (result["doc_id"],)
        ).fetchone()
        assert "fund manager" in section["text"]

    def test_pdf_with_no_extractable_text_yields_zero_confidence(self):
        """A PDF whose content stream yields no text (standing in for a scanned/
        image-only page, which parse_pdf() never OCRs per MVP scope) must not silently
        claim full confidence — parse_confidence must reflect the empty extraction, even
        though a generic 'addendum' fallback event still gets recorded (see the HTML
        no-specific-signal test above for why that fallback exists)."""
        conn = _in_memory_db()
        raw = _minimal_pdf_bytes("")
        result = ingest_addendum(
            conn, raw, url="https://example-amc.com/addendum-4.pdf",
            scheme_id="TEST_SCHEME", doc_date="2026-09-01",
        )
        doc = conn.execute(
            "SELECT * FROM document WHERE doc_id = ?", (result["doc_id"],)
        ).fetchone()
        assert doc["parse_confidence"] == pytest.approx(0.0)
        assert result["change_events"] == 1
        assert result["signals"][0]["event_type"] == "addendum"
