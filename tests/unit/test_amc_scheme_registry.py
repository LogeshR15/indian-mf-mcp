"""Unit tests for ingest/amc_scheme_registry.py — scheme-to-adapter-hint mapping."""
from __future__ import annotations

import sqlite3
import pytest

from indian_mf_mcp.ingest.amc_scheme_registry import (
    upsert_scheme_hint,
    get_schemes_for_amc,
    get_hint_for_scheme,
    list_amc_ids_with_hints,
    auto_register_from_navall,
)


def _in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        conn.executescript(f.read())
    return conn


def _seed_scheme(conn, scheme_id="SCH_001", amc_id="ppfas", name="PPFAS Flexi Cap Fund",
                 active=1):
    conn.execute(
        "INSERT OR IGNORE INTO amc (amc_id, name) VALUES (?, ?)",
        (amc_id, amc_id.upper()),
    )
    conn.execute(
        """INSERT OR IGNORE INTO scheme
           (scheme_id, amc_id, name, scheme_type, category, active)
           VALUES (?, ?, ?, 'open_ended', 'Flexi Cap', ?)""",
        (scheme_id, amc_id, name, active),
    )
    conn.commit()


class TestUpsertAndGet:
    def test_upsert_and_retrieve(self):
        conn = _in_memory_db()
        _seed_scheme(conn)
        upsert_scheme_hint(conn, "SCH_001", "ppfas", "Parag Parikh Flexi Cap Fund")
        conn.commit()
        hint = get_hint_for_scheme(conn, "SCH_001", "ppfas")
        assert hint == "Parag Parikh Flexi Cap Fund"

    def test_upsert_replaces_existing(self):
        conn = _in_memory_db()
        _seed_scheme(conn)
        upsert_scheme_hint(conn, "SCH_001", "ppfas", "Old Hint")
        conn.commit()
        upsert_scheme_hint(conn, "SCH_001", "ppfas", "New Hint")
        conn.commit()
        hint = get_hint_for_scheme(conn, "SCH_001", "ppfas")
        assert hint == "New Hint"

    def test_get_hint_missing_returns_none(self):
        conn = _in_memory_db()
        assert get_hint_for_scheme(conn, "NONEXISTENT", "ppfas") is None

    def test_different_amc_different_hint(self):
        conn = _in_memory_db()
        _seed_scheme(conn, "SCH_001", "ppfas", "PPFAS Fund")
        _seed_scheme(conn, "SCH_001", "hdfc", "PPFAS Fund")  # same scheme, different AMC
        upsert_scheme_hint(conn, "SCH_001", "ppfas", "Hint for PPFAS")
        upsert_scheme_hint(conn, "SCH_001", "hdfc", "Hint for HDFC")
        conn.commit()
        assert get_hint_for_scheme(conn, "SCH_001", "ppfas") == "Hint for PPFAS"
        assert get_hint_for_scheme(conn, "SCH_001", "hdfc") == "Hint for HDFC"


class TestGetSchemesForAmc:
    def test_returns_all_schemes(self):
        conn = _in_memory_db()
        _seed_scheme(conn, "SCH_001", "ppfas", "PPFAS Flexi Cap")
        _seed_scheme(conn, "SCH_002", "ppfas", "PPFAS Tax Saver")
        upsert_scheme_hint(conn, "SCH_001", "ppfas", "Flexi Cap Fund")
        upsert_scheme_hint(conn, "SCH_002", "ppfas", "Tax Saver Fund")
        conn.commit()
        schemes = get_schemes_for_amc(conn, "ppfas")
        assert len(schemes) == 2

    def test_returns_empty_for_unknown_amc(self):
        conn = _in_memory_db()
        assert get_schemes_for_amc(conn, "NONEXISTENT") == []

    def test_sorted_by_name(self):
        conn = _in_memory_db()
        _seed_scheme(conn, "SCH_B", "ppfas", "ZZZZ Fund")
        _seed_scheme(conn, "SCH_A", "ppfas", "AAAA Fund")
        upsert_scheme_hint(conn, "SCH_B", "ppfas", "Hint B")
        upsert_scheme_hint(conn, "SCH_A", "ppfas", "Hint A")
        conn.commit()
        schemes = get_schemes_for_amc(conn, "ppfas")
        assert schemes[0]["name"] == "AAAA Fund"
        assert schemes[1]["name"] == "ZZZZ Fund"

    def test_returned_dict_has_required_keys(self):
        conn = _in_memory_db()
        _seed_scheme(conn, "SCH_001", "ppfas", "PPFAS Flexi Cap")
        upsert_scheme_hint(conn, "SCH_001", "ppfas", "Flexi Cap Fund")
        conn.commit()
        schemes = get_schemes_for_amc(conn, "ppfas")
        assert len(schemes) == 1
        s = schemes[0]
        assert "scheme_id" in s
        assert "adapter_hint" in s
        assert "name" in s
        assert "active" in s


class TestListAmcIdsWithHints:
    def test_empty_when_no_hints(self):
        conn = _in_memory_db()
        assert list_amc_ids_with_hints(conn) == []

    def test_returns_distinct_amc_ids(self):
        conn = _in_memory_db()
        _seed_scheme(conn, "SCH_001", "ppfas", "Fund A")
        _seed_scheme(conn, "SCH_002", "hdfc", "Fund B")
        _seed_scheme(conn, "SCH_003", "ppfas", "Fund C")
        upsert_scheme_hint(conn, "SCH_001", "ppfas", "Hint A")
        upsert_scheme_hint(conn, "SCH_002", "hdfc", "Hint B")
        upsert_scheme_hint(conn, "SCH_003", "ppfas", "Hint C")
        conn.commit()
        ids = list_amc_ids_with_hints(conn)
        assert "ppfas" in ids
        assert "hdfc" in ids
        assert len(ids) == 2  # deduplicated

    def test_sorted_alphabetically(self):
        conn = _in_memory_db()
        _seed_scheme(conn, "S1", "zzz_amc", "Z Fund")
        _seed_scheme(conn, "S2", "aaa_amc", "A Fund")
        upsert_scheme_hint(conn, "S1", "zzz_amc", "H1")
        upsert_scheme_hint(conn, "S2", "aaa_amc", "H2")
        conn.commit()
        ids = list_amc_ids_with_hints(conn)
        assert ids[0] == "aaa_amc"
        assert ids[1] == "zzz_amc"


class TestAutoRegisterFromNavall:
    def test_registers_unregistered_schemes(self):
        conn = _in_memory_db()
        _seed_scheme(conn, "SCH_001", "ppfas", "PPFAS Flexi Cap Fund")
        count = auto_register_from_navall(conn)
        conn.commit()
        assert count >= 1
        hint = get_hint_for_scheme(conn, "SCH_001", "ppfas")
        assert hint == "PPFAS Flexi Cap Fund"

    def test_does_not_override_manual_hints(self):
        """Existing hints should NOT be replaced by auto-registration."""
        conn = _in_memory_db()
        _seed_scheme(conn, "SCH_001", "ppfas", "PPFAS Flexi Cap Fund")
        upsert_scheme_hint(conn, "SCH_001", "ppfas", "Manual Hint Override")
        conn.commit()
        auto_register_from_navall(conn)
        conn.commit()
        hint = get_hint_for_scheme(conn, "SCH_001", "ppfas")
        assert hint == "Manual Hint Override"

    def test_skips_inactive_schemes(self):
        conn = _in_memory_db()
        _seed_scheme(conn, "SCH_DEAD", "ppfas", "Old Dead Fund", active=0)
        count = auto_register_from_navall(conn)
        conn.commit()
        # Inactive scheme should not be registered
        hint = get_hint_for_scheme(conn, "SCH_DEAD", "ppfas")
        assert hint is None

    def test_skips_schemes_without_amc(self):
        conn = _in_memory_db()
        # Scheme with no amc_id (edge case)
        conn.execute(
            "INSERT INTO scheme (scheme_id, amc_id, name, active) VALUES ('ORPHAN', NULL, 'Orphan Fund', 1)"
        )
        conn.commit()
        count = auto_register_from_navall(conn)
        # Should not crash; orphan is excluded (amc_id IS NOT NULL filter)
        hint = get_hint_for_scheme(conn, "ORPHAN", "")
        assert hint is None
