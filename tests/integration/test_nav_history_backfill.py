from datetime import date
from pathlib import Path

from indian_mf_mcp.ingest import amfi_nav_history as navhist
from indian_mf_mcp.ingest.amfi_navall import run_daily_ingest
from indian_mf_mcp.store.db import get_connection

NAVALL = Path(__file__).parent.parent / "fixtures" / "sample_navall_v2.txt"
HISTORY = Path(__file__).parent.parent / "fixtures" / "sample_nav_history.txt"


def _seeded_conn(tmp_path, name="hist.db"):
    """A store whose plan universe has been established by the daily NAVAll ingest."""
    conn = get_connection(tmp_path / name)
    run_daily_ingest(conn, raw=NAVALL.read_bytes(), as_of=date(2026, 9, 10))
    return conn


def test_history_populates_multi_day_nav_series(tmp_path):
    conn = _seeded_conn(tmp_path)
    stats = navhist.ingest_history_text(conn, HISTORY.read_text())

    assert stats["nav_points"] == 4
    navs = conn.execute(
        "SELECT date, nav FROM nav_point WHERE plan_id = 'plan-122639' ORDER BY date"
    ).fetchall()
    # The trailing 2026-09-10 point is the daily NAVAll snapshot the store was seeded with;
    # history fills in the series around it rather than replacing it.
    assert [(r[0], r[1]) for r in navs] == [
        ("2026-09-01", 90.7896), ("2026-09-02", 90.5023), ("2026-09-03", 90.6349),
        ("2026-09-10", 75.1234),
    ]
    conn.close()


def test_unknown_scheme_codes_are_skipped_not_invented(tmp_path):
    """Scheme identity comes from the daily NAVAll ingest; a history file must not create
    half-populated scheme rows for codes the store has never seen."""
    conn = _seeded_conn(tmp_path)
    stats = navhist.ingest_history_text(conn, HISTORY.read_text())

    assert stats["unknown_scheme_codes"] == 1  # the Taurus code, absent from the NAVAll fixture
    assert conn.execute("SELECT count(*) FROM plan WHERE plan_id = 'plan-999999'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM nav_point WHERE plan_id = 'plan-999999'").fetchone()[0] == 0
    conn.close()


def test_ingest_is_idempotent(tmp_path):
    conn = _seeded_conn(tmp_path)
    navhist.ingest_history_text(conn, HISTORY.read_text())
    before = conn.execute("SELECT count(*) FROM nav_point").fetchone()[0]
    navhist.ingest_history_text(conn, HISTORY.read_text())
    assert conn.execute("SELECT count(*) FROM nav_point").fetchone()[0] == before
    conn.close()


def test_backfill_chunks_by_month_and_resumes(tmp_path, monkeypatch):
    conn = _seeded_conn(tmp_path)
    calls: list[tuple] = []

    def fake_fetch(frm, to, tp="1", client=None):
        calls.append((frm, to, tp))
        return HISTORY.read_text() if tp == "1" else ""

    monkeypatch.setattr(navhist, "fetch_nav_history", fake_fetch)

    stats = navhist.backfill_nav_history(conn, date(2026, 8, 15), date(2026, 9, 3))
    assert stats["chunks"] == 2
    assert stats["chunks_skipped"] == 0
    assert [(c[0], c[1]) for c in calls if c[2] == "1"] == [
        (date(2026, 8, 15), date(2026, 8, 31)),
        (date(2026, 9, 1), date(2026, 9, 3)),
    ]
    assert {c[2] for c in calls} == {"1", "2", "3"}

    # A rerun must not re-download months already recorded as ingested.
    calls.clear()
    rerun = navhist.backfill_nav_history(conn, date(2026, 8, 15), date(2026, 9, 3))
    assert rerun["chunks_skipped"] == 2
    assert rerun["chunks"] == 0
    assert calls == []

    # ...unless forced.
    forced = navhist.backfill_nav_history(conn, date(2026, 8, 15), date(2026, 9, 3), force=True)
    assert forced["chunks"] == 2
    conn.close()


def test_failed_month_is_recorded_and_does_not_abort_the_backfill(tmp_path, monkeypatch):
    import httpx

    conn = _seeded_conn(tmp_path)

    def flaky_fetch(frm, to, tp="1", client=None):
        if frm.month == 8:
            raise httpx.ConnectError("boom")
        return HISTORY.read_text() if tp == "1" else ""

    monkeypatch.setattr(navhist, "fetch_nav_history", flaky_fetch)
    stats = navhist.backfill_nav_history(conn, date(2026, 8, 1), date(2026, 9, 3))

    assert stats["chunks"] == 1  # September still committed
    assert stats["nav_points"] == 4
    assert len(stats["warnings"]) == 1 and "fetch failed" in stats["warnings"][0]

    failed = conn.execute(
        "SELECT status FROM ingest_run WHERE run_id = 'navhist-2026-08-01_2026-08-31'"
    ).fetchone()
    assert failed[0] == "failed"

    # The failed month must not be skipped as "done" on the next run.
    monkeypatch.setattr(navhist, "fetch_nav_history",
                        lambda frm, to, tp="1", client=None: HISTORY.read_text() if tp == "1" else "")
    retry = navhist.backfill_nav_history(conn, date(2026, 8, 1), date(2026, 9, 3))
    assert retry["chunks"] == 1 and retry["chunks_skipped"] == 1
    conn.close()


def test_backfill_into_empty_store_warns(tmp_path, monkeypatch):
    """No plans known yet means every history row is unmatched — a real misordering of the
    setup steps, not a silent no-op."""
    conn = get_connection(tmp_path / "empty.db")
    monkeypatch.setattr(navhist, "fetch_nav_history",
                        lambda frm, to, tp="1", client=None: HISTORY.read_text() if tp == "1" else "")
    stats = navhist.backfill_nav_history(conn, date(2026, 9, 1), date(2026, 9, 3))

    assert stats["nav_points"] == 0
    assert stats["warnings"] and "ingest-navall" in stats["warnings"][0]
    conn.close()
