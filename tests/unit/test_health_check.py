"""Unit tests for ingest/health_check.py — adapter health check logic."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from indian_mf_mcp.ingest.health_check import (
    HealthResult,
    check_adapter,
    run_health_checks,
    save_health_results,
    format_health_table,
    _EXPECTED_GAP,
)
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef


def _in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        conn.executescript(f.read())
    return conn


def _make_adapter(amc_id: str, refs: list[DocumentRef] | Exception):
    """Build a mock adapter that returns refs or raises an exception."""
    adapter = MagicMock()
    adapter.amc_id = amc_id
    if isinstance(refs, Exception):
        adapter.list_documents.side_effect = refs
    else:
        adapter.list_documents.return_value = refs
    return adapter


def _ref(as_of_date: date) -> DocumentRef:
    return DocumentRef(
        url=f"https://example.com/{as_of_date}.xlsx",
        doc_type=DocType.MONTHLY_PORTFOLIO,
        as_of_date=as_of_date,
    )


class TestCheckAdapter:
    def test_ok_status_recent_doc(self):
        today = date.today()
        adapter = _make_adapter("ppfas", [_ref(today - timedelta(days=5))])
        result = check_adapter(adapter, "monthly_portfolio")
        assert result.status == "ok"
        assert result.amc_id == "ppfas"
        assert result.error is None

    def test_broken_when_raises(self):
        adapter = _make_adapter("hdfc", ConnectionError("Site down"))
        result = check_adapter(adapter, "monthly_portfolio")
        assert result.status == "broken"
        assert result.error is not None
        assert "Site down" in result.error

    def test_degraded_when_no_docs(self):
        adapter = _make_adapter("sbi", [])
        result = check_adapter(adapter, "monthly_portfolio")
        assert result.status == "degraded"
        assert result.last_doc_date is None

    def test_degraded_when_doc_too_old(self):
        # Monthly portfolio: expected gap is 35 days
        old_date = date.today() - timedelta(days=60)
        adapter = _make_adapter("uti", [_ref(old_date)])
        result = check_adapter(adapter, "monthly_portfolio")
        assert result.status == "degraded"
        assert result.actual_gap_days is not None
        assert result.actual_gap_days > _EXPECTED_GAP["monthly_portfolio"]

    def test_ok_picks_most_recent_ref(self):
        today = date.today()
        adapter = _make_adapter("axis", [
            _ref(today - timedelta(days=60)),  # old
            _ref(today - timedelta(days=5)),   # recent
        ])
        result = check_adapter(adapter, "monthly_portfolio")
        assert result.status == "ok"
        assert result.last_doc_date == today - timedelta(days=5)

    def test_unknown_doc_type_is_broken(self):
        adapter = _make_adapter("ppfas", [])
        result = check_adapter(adapter, "not_a_real_doc_type")
        assert result.status == "broken"
        assert "Unknown doc_type" in result.error

    def test_checked_at_is_set(self):
        adapter = _make_adapter("ppfas", [])
        result = check_adapter(adapter, "monthly_portfolio")
        assert isinstance(result.checked_at, datetime)

    def test_refs_without_dates_dont_crash(self):
        adapter = _make_adapter("ppfas", [
            DocumentRef(url="https://example.com/f.pdf",
                        doc_type=DocType.FACTSHEET, as_of_date=None)
        ])
        result = check_adapter(adapter, "factsheet")
        # No as_of_date → should not raise; may be ok or degraded depending on fallback
        assert result.status in ("ok", "degraded", "broken")

    def test_expected_gap_used_for_addendum(self):
        """Addenda are irregular; expected gap is 180 days."""
        old_date = date.today() - timedelta(days=90)
        adapter = _make_adapter("ppfas", [_ref(old_date)])
        result = check_adapter(adapter, "addendum")
        # 90 days < 180 day expected gap for addenda → should be ok
        assert result.status == "ok"
        assert result.expected_gap_days == _EXPECTED_GAP["addendum"]


class TestRunHealthChecks:
    def test_checks_all_adapters_and_types(self):
        adapters = [
            _make_adapter("ppfas", [_ref(date.today() - timedelta(days=5))]),
            _make_adapter("hdfc", [_ref(date.today() - timedelta(days=10))]),
        ]
        results = run_health_checks(adapters, doc_types=["monthly_portfolio"])
        assert len(results) == 2
        assert all(r.status == "ok" for r in results)

    def test_broken_first_in_results(self):
        adapters = [
            _make_adapter("aaaa", [_ref(date.today())]),               # ok
            _make_adapter("zzzz", ConnectionError("Broken")),           # broken
        ]
        results = run_health_checks(adapters, doc_types=["monthly_portfolio"])
        assert results[0].status == "broken"

    def test_default_doc_types(self):
        adapter = _make_adapter("ppfas", [_ref(date.today() - timedelta(days=5))])
        results = run_health_checks([adapter])  # no doc_types → uses default
        # Default = monthly_portfolio + factsheet
        assert len(results) == 2

    def test_exception_in_future_captured(self):
        """If an adapter raises during thread execution, result is 'broken' not a crash."""
        adapter = _make_adapter("bad_amc", RuntimeError("Unexpected"))
        results = run_health_checks([adapter], doc_types=["monthly_portfolio"])
        assert len(results) == 1
        assert results[0].status == "broken"


class TestSaveHealthResults:
    def test_saves_to_db(self):
        conn = _in_memory_db()
        result = HealthResult(
            amc_id="ppfas",
            doc_type="monthly_portfolio",
            status="ok",
            last_doc_date=date.today(),
            expected_gap_days=35,
            actual_gap_days=5,
            error=None,
            checked_at=datetime.now(timezone.utc),
        )
        save_health_results(conn, [result])
        row = conn.execute(
            "SELECT * FROM adapter_health WHERE amc_id = 'ppfas'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "ok"
        assert row["actual_gap_days"] == 5

    def test_upserts_on_repeated_save(self):
        conn = _in_memory_db()
        r1 = HealthResult(
            amc_id="ppfas", doc_type="monthly_portfolio", status="ok",
            last_doc_date=date.today(), expected_gap_days=35, actual_gap_days=5,
            error=None, checked_at=datetime.now(timezone.utc),
        )
        r2 = HealthResult(
            amc_id="ppfas", doc_type="monthly_portfolio", status="broken",
            last_doc_date=None, expected_gap_days=35, actual_gap_days=None,
            error="Site down", checked_at=datetime.now(timezone.utc),
        )
        save_health_results(conn, [r1])
        save_health_results(conn, [r2])
        rows = conn.execute("SELECT * FROM adapter_health WHERE amc_id = 'ppfas'").fetchall()
        assert len(rows) == 1  # upserted, not duplicated
        assert rows[0]["status"] == "broken"

    def test_saves_error_text(self):
        conn = _in_memory_db()
        result = HealthResult(
            amc_id="hdfc", doc_type="factsheet", status="broken",
            last_doc_date=None, expected_gap_days=35, actual_gap_days=None,
            error="Connection timeout after 60s",
            checked_at=datetime.now(timezone.utc),
        )
        save_health_results(conn, [result])
        row = conn.execute("SELECT error FROM adapter_health WHERE amc_id='hdfc'").fetchone()
        assert "timeout" in row["error"]


class TestFormatHealthTable:
    def test_returns_string(self):
        result = HealthResult(
            amc_id="ppfas", doc_type="monthly_portfolio", status="ok",
            last_doc_date=date.today(), expected_gap_days=35, actual_gap_days=10,
            error=None, checked_at=datetime.now(timezone.utc),
        )
        output = format_health_table([result])
        assert isinstance(output, str)
        assert "ppfas" in output
        assert "ok" in output

    def test_empty_list(self):
        output = format_health_table([])
        assert "No health checks" in output

    def test_status_icons_present(self):
        results = [
            HealthResult("a", "monthly_portfolio", "ok", date.today(), 35, 5, None, datetime.now(timezone.utc)),
            HealthResult("b", "monthly_portfolio", "degraded", date.today() - timedelta(days=50), 35, 50, "Stale", datetime.now(timezone.utc)),
            HealthResult("c", "monthly_portfolio", "broken", None, 35, None, "Error", datetime.now(timezone.utc)),
        ]
        output = format_health_table(results)
        assert "✓" in output
        assert "⚠" in output
        assert "✗" in output

    def test_health_result_to_dict(self):
        result = HealthResult(
            amc_id="ppfas", doc_type="monthly_portfolio", status="ok",
            last_doc_date=date(2026, 8, 31), expected_gap_days=35, actual_gap_days=10,
            error=None, checked_at=datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc),
        )
        d = result.to_dict()
        assert d["amc_id"] == "ppfas"
        assert d["status"] == "ok"
        assert d["last_doc_date"] == "2026-08-31"
        assert d["actual_gap_days"] == 10
