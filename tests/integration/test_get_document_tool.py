import json
from pathlib import Path

from indian_mf_mcp.store.blobstore import put
from indian_mf_mcp.parsers.pdf_text import parse_pdf
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_document import get_document

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_sid.pdf"


def _seed_doc(conn, doc_id="doc-test-sid"):
    raw = FIXTURE.read_bytes()
    sha256, _ = put(raw)
    result = parse_pdf(raw)
    conn.execute(
        """INSERT INTO document (doc_id, doc_type, doc_date, source_url, sha256, content_type,
             blob_path, retrieved_at, page_count, parse_status, parse_confidence)
           VALUES (?, 'SID', '2026-01-01', 'https://example.test/sid.pdf', ?, 'pdf', 'x',
                   '2026-09-12T00:00:00Z', ?, 'parsed', ?)""",
        (doc_id, sha256, result.page_count, result.parse_confidence),
    )
    conn.executemany(
        "INSERT INTO document_section (doc_id, page_number, headings_json, text) VALUES (?, ?, ?, ?)",
        [(doc_id, p.page_number, json.dumps(p.headings), p.text) for p in result.pages],
    )
    conn.commit()
    return doc_id


def test_get_document_by_doc_id_with_query(tmp_path):
    conn = get_connection(tmp_path / "doc.db")
    doc_id = _seed_doc(conn)

    out = get_document(conn, doc_id=doc_id, query="investment objective", max_chars=5000)
    assert "excerpts" in out["data"]
    excerpts = out["data"]["excerpts"]["v"]
    assert len(excerpts) > 0
    assert all("page_number" in e for e in excerpts)
    assert out["data"]["excerpts"]["k"] == "official"
    conn.close()


def test_get_document_unknown_returns_explicit_not_found(tmp_path):
    conn = get_connection(tmp_path / "doc2.db")
    out = get_document(conn, doc_id="doc-nonexistent")
    assert out["error"] == "document_not_found"
    conn.close()


def test_get_document_sections_filter_by_heading(tmp_path):
    conn = get_connection(tmp_path / "doc3.db")
    doc_id = _seed_doc(conn)
    out = get_document(conn, doc_id=doc_id, sections=["Risk Factors"], max_chars=10000)
    excerpts = out["data"]["excerpts"]["v"]
    assert excerpts
    assert all("Risk Factors" in e["headings"] for e in excerpts)
    conn.close()
