"""Unit tests for Phase 3 manager_repository store helpers."""
from __future__ import annotations

import json
import sqlite3

import pytest

from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.store import manager_repository as mrep


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    c = get_connection(db_path)
    # Seed a minimal scheme + plan so FK constraints are satisfied
    c.execute("INSERT INTO amc (amc_id, name) VALUES ('amc-1', 'Test AMC')")
    c.execute(
        "INSERT INTO scheme (scheme_id, amc_id, name, active, first_seen, last_seen) "
        "VALUES ('scheme-1', 'amc-1', 'Test Flexi Cap', 1, '2020-01-01', '2026-01-01')"
    )
    c.execute(
        "INSERT INTO plan (plan_id, scheme_id, amfi_scheme_code, plan_type, option_type, active, first_seen, last_seen) "
        "VALUES ('plan-1', 'scheme-1', '999001', 'Direct', 'Growth', 1, '2020-01-01', '2026-01-01')"
    )
    c.commit()
    yield c
    c.close()


class TestUpsertManager:
    def test_creates_manager(self, conn):
        mgr_id = mrep.upsert_manager(conn, "Rajeev Thakkar")
        conn.commit()
        row = conn.execute("SELECT * FROM manager WHERE manager_id = ?", (mgr_id,)).fetchone()
        assert row is not None
        assert row["name_normalised"] == "Rajeev Thakkar"

    def test_idempotent_double_insert(self, conn):
        id1 = mrep.upsert_manager(conn, "Rajeev Thakkar")
        id2 = mrep.upsert_manager(conn, "Rajeev Thakkar")
        conn.commit()
        assert id1 == id2
        count = conn.execute("SELECT COUNT(*) FROM manager").fetchone()[0]
        assert count == 1

    def test_alias_added(self, conn):
        mgr_id = mrep.upsert_manager(conn, "Rajeev Thakkar", alias="R. Thakkar")
        conn.commit()
        row = conn.execute("SELECT aliases_json FROM manager WHERE manager_id = ?", (mgr_id,)).fetchone()
        aliases = json.loads(row["aliases_json"])
        assert "R. Thakkar" in aliases

    def test_second_alias_merged(self, conn):
        mgr_id = mrep.upsert_manager(conn, "Rajeev Thakkar", alias="R. Thakkar")
        mrep.upsert_manager(conn, "Rajeev Thakkar", alias="Rajeev T.")
        conn.commit()
        row = conn.execute("SELECT aliases_json FROM manager WHERE manager_id = ?", (mgr_id,)).fetchone()
        aliases = json.loads(row["aliases_json"])
        assert "R. Thakkar" in aliases
        assert "Rajeev T." in aliases


class TestManagerAssignment:
    def test_upsert_and_query(self, conn):
        mgr_id = mrep.upsert_manager(conn, "Rajeev Thakkar")
        asgn_id = mrep.upsert_manager_assignment(
            conn, "scheme-1", mgr_id, from_date="2013-05-28"
        )
        conn.commit()
        rows = mrep.get_current_managers_for_scheme(conn, "scheme-1")
        assert len(rows) == 1
        assert rows[0]["name_normalised"] == "Rajeev Thakkar"
        assert rows[0]["to_date"] is None

    def test_close_assignment(self, conn):
        mgr_id = mrep.upsert_manager(conn, "Rajeev Thakkar")
        mrep.upsert_manager_assignment(conn, "scheme-1", mgr_id, from_date="2013-05-28")
        conn.commit()
        mrep.close_assignment(conn, "scheme-1", mgr_id, to_date="2025-01-01")
        conn.commit()
        current = mrep.get_current_managers_for_scheme(conn, "scheme-1")
        assert len(current) == 0
        all_asgns = mrep.get_managers_for_scheme(conn, "scheme-1")
        assert len(all_asgns) == 1
        assert all_asgns[0]["to_date"] == "2025-01-01"


class TestChangeEvent:
    def test_insert_and_query(self, conn):
        evt_id = mrep.insert_change_event(
            conn, "scheme-1", event_type="manager_change",
            detected_date="2025-06-01",
            before={"managers": ["Old Manager"]},
            after={"managers": ["New Manager"]},
        )
        conn.commit()
        events = mrep.get_change_events(conn, "scheme-1", event_type="manager_change")
        assert len(events) == 1
        assert events[0]["event_type"] == "manager_change"
        assert json.loads(events[0]["before_json"]) == {"managers": ["Old Manager"]}

    def test_idempotent(self, conn):
        mrep.insert_change_event(conn, "scheme-1", "ter_change", "2025-06-01",
                                  before={"ter": 0.5}, after={"ter": 0.6})
        mrep.insert_change_event(conn, "scheme-1", "ter_change", "2025-06-01",
                                  before={"ter": 0.5}, after={"ter": 0.6})
        conn.commit()
        # Should not raise; idempotent due to ON CONFLICT DO NOTHING
        events = mrep.get_change_events(conn, "scheme-1", event_type="ter_change")
        assert len(events) == 1

    def test_since_filter(self, conn):
        mrep.insert_change_event(conn, "scheme-1", "ter_change", "2024-01-01")
        mrep.insert_change_event(conn, "scheme-1", "ter_change", "2025-06-01")
        conn.commit()
        events = mrep.get_change_events(conn, "scheme-1", event_type="ter_change",
                                         since="2025-01-01")
        assert len(events) == 1
        assert events[0]["detected_date"] == "2025-06-01"


class TestTERHistory:
    def test_upsert_and_get_latest(self, conn):
        mrep.upsert_ter(conn, "plan-1", "2026-08-01", ter_pct=0.63, source="factsheet")
        conn.commit()
        row = mrep.get_latest_ter(conn, "plan-1")
        assert row is not None
        assert abs(row["ter_pct"] - 0.63) < 1e-9

    def test_update_same_date(self, conn):
        mrep.upsert_ter(conn, "plan-1", "2026-08-01", ter_pct=0.63)
        mrep.upsert_ter(conn, "plan-1", "2026-08-01", ter_pct=0.61)
        conn.commit()
        row = mrep.get_latest_ter(conn, "plan-1")
        assert abs(row["ter_pct"] - 0.61) < 1e-9

    def test_get_history_since(self, conn):
        mrep.upsert_ter(conn, "plan-1", "2025-01-01", ter_pct=0.70)
        mrep.upsert_ter(conn, "plan-1", "2026-01-01", ter_pct=0.65)
        mrep.upsert_ter(conn, "plan-1", "2026-08-01", ter_pct=0.63)
        conn.commit()
        rows = mrep.get_ter_history(conn, "plan-1", since="2026-01-01")
        assert len(rows) == 2
        assert rows[0]["as_of_date"] == "2026-01-01"
