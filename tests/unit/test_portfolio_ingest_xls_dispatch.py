"""Regression test: ingest_scheme_portfolios() must route a legacy .xls (BIFF) file through
parse_portfolio_xls() and actually persist it, not silently count it as skipped_format the
way it did before parse_portfolio_xls existed.
"""
from __future__ import annotations

import io
import sqlite3
from datetime import date

import xlwt

from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import portfolio_repository as prepo


def _in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        conn.executescript(f.read())
    return conn


def _build_xls() -> bytes:
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    rows = [
        ["Name of the Instrument", "ISIN", "Industry", "Quantity",
         "Market Value (Rs. in Lakhs)", "% to Net Assets"],
        ["Equity & Equity Related", None, None, None, None, None],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 1000, 1500.0, 0.6],
        ["Infosys Limited", "INE009A01021", "IT - Software", 800, 1000.0, 0.4],
        ["Grand Total", None, None, None, 2500.0, 1.0],
    ]
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            if val is not None:
                ws.write(r, c, val)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class _StubXlsAdapter:
    amc_id = "stub-xls-amc"

    def __init__(self, raw: bytes):
        self._raw = raw

    def list_documents(self, doc_type, since, scheme_hint=None, client=None):
        return [DocumentRef(url="https://example-amc.com/portfolio-aug2026.xls",
                             doc_type=doc_type, as_of_date=date(2026, 8, 31))]

    def fetch(self, ref, client=None):
        return self._raw


def test_xls_file_is_ingested_not_skipped():
    conn = _in_memory_db()
    adapter = _StubXlsAdapter(_build_xls())

    stats = ingest_scheme_portfolios(
        conn, adapter, scheme_id="scheme-1", scheme_hint="Test Fund", since=date(2026, 1, 1),
    )

    assert stats["skipped_format"] == 0
    assert stats["skipped_reconciliation_failed"] == 0
    assert stats["ingested"] == 1

    snap = prepo.get_latest_snapshot(conn, "scheme-1")
    assert snap is not None
    holdings = prepo.get_holdings(conn, snap["snapshot_id"])
    assert len(holdings) == 2

    doc = conn.execute(
        "SELECT content_type FROM document WHERE scheme_id = 'scheme-1'"
    ).fetchone()
    assert doc["content_type"] == "xls"


def test_html_masquerading_as_xls_is_still_skipped_not_misparsed():
    conn = _in_memory_db()
    adapter = _StubXlsAdapter(b"<html><body>not really a spreadsheet</body></html>")

    stats = ingest_scheme_portfolios(
        conn, adapter, scheme_id="scheme-2", scheme_hint="Test Fund", since=date(2026, 1, 1),
    )

    assert stats["skipped_format"] == 1
    assert stats["ingested"] == 0


def _build_multi_sheet_xls() -> bytes:
    """A combined workbook covering more than one scheme — the shape some AMCs' archives
    quietly switch to in older months even for an adapter normally configured one-file-
    per-scheme (sheet_resolver=None)."""
    wb = xlwt.Workbook()
    rows = [
        ["Name of the Instrument", "ISIN", "Industry", "Quantity",
         "Market Value (Rs. in Lakhs)", "% to Net Assets"],
        ["Equity & Equity Related", None, None, None, None, None],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 1000, 1500.0, 1.0],
        ["Grand Total", None, None, None, 1500.0, 1.0],
    ]
    for sheet_name in ("SchemeA", "SchemeB"):
        ws = wb.add_sheet(sheet_name)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                if val is not None:
                    ws.write(r, c, val)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_multi_sheet_file_with_no_sheet_resolver_is_flagged_not_silently_guessed():
    """A one-file-per-scheme adapter (sheet_resolver=None) that actually receives a
    multi-sheet combined workbook must not silently parse "sheet 0" and attribute
    possibly-wrong holdings to this scheme_id — must be flagged and skipped instead."""
    conn = _in_memory_db()
    adapter = _StubXlsAdapter(_build_multi_sheet_xls())

    stats = ingest_scheme_portfolios(
        conn, adapter, scheme_id="scheme-3", scheme_hint="Test Fund", since=date(2026, 1, 1),
    )

    assert stats["skipped_ambiguous_multi_sheet"] == 1
    assert stats["ingested"] == 0

    # The raw bytes must still be archived (never evicted), even though no snapshot exists.
    doc = conn.execute(
        "SELECT * FROM document WHERE scheme_id = 'scheme-3'"
    ).fetchone()
    assert doc is not None
    snap = prepo.get_latest_snapshot(conn, "scheme-3")
    assert snap is None
