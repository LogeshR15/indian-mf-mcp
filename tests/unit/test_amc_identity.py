"""AMC identity mapping — the join between adapter keys, adapter-declared ids, and DB ids.

These three namespaces drifted apart silently before amc_identity existed: bulk backfill
found zero schemes and adapter-health staleness warnings never matched a row, with no test
failing. The sync assertions below are the guard — adding an adapter without registering
its AMFI name now fails here rather than at a user's first backfill.
"""
from __future__ import annotations

import sqlite3

import pytest

from indian_mf_mcp.ingest import amc_identity
from indian_mf_mcp.ingest.amc_adapters.registry import ADAPTERS, list_amc_ids
from indian_mf_mcp.ingest.amc_identity import (
    AMFI_AMC_NAME,
    ADAPTER_DECLARED_AMC_ID,
    adapter_key_for_amc_id,
    amc_id_for,
    amc_ids_for_adapter,
    health_lookup_ids,
    present_amc_ids_for_adapter,
)


class TestRegistrySync:
    def test_every_adapter_has_an_amfi_name(self):
        assert set(ADAPTERS) == set(AMFI_AMC_NAME)

    def test_every_adapter_has_a_declared_id_entry(self):
        assert set(ADAPTERS) == set(ADAPTER_DECLARED_AMC_ID)

    def test_declared_ids_match_the_adapter_classes(self):
        for key, (adapter_cls, _) in ADAPTERS.items():
            assert ADAPTER_DECLARED_AMC_ID[key] == adapter_cls.amc_id, (
                f"{key}: amc_identity says {ADAPTER_DECLARED_AMC_ID[key]!r}, "
                f"the adapter class declares {adapter_cls.amc_id!r}"
            )

    def test_amfi_names_are_distinct(self):
        names = list(AMFI_AMC_NAME.values())
        assert len(names) == len(set(names))

    def test_list_amc_ids_matches_registry(self):
        assert list_amc_ids() == sorted(ADAPTERS)


class TestResolution:
    def test_amfi_name_slugs_to_db_amc_id(self):
        # This is exactly what ingest writes for a PPFAS row in NAVAll.txt.
        assert amc_id_for("PPFAS Mutual Fund") == "amc-ppfas-mutual-fund"
        assert amc_ids_for_adapter("ppfas")[0] == "amc-ppfas-mutual-fund"

    def test_round_trip_for_every_adapter(self):
        for key in AMFI_AMC_NAME:
            for amc_id in amc_ids_for_adapter(key):
                assert adapter_key_for_amc_id(amc_id) == key

    def test_aliases_resolve_to_the_same_adapter(self):
        # AMFI used the full legal name before switching to "PPFAS Mutual Fund"; schemes
        # ingested then keep the old amc_id forever.
        legacy = amc_id_for("Parag Parikh Financial Advisory Services Private Limited")
        assert adapter_key_for_amc_id(legacy) == "ppfas"

    def test_current_name_is_listed_before_aliases(self):
        ids = amc_ids_for_adapter("ppfas")
        assert ids[0] == "amc-ppfas-mutual-fund"
        assert len(ids) > 1

    def test_amc_without_an_adapter_resolves_to_none(self):
        # ~23 of AMFI's ~54 AMCs have no portfolio adapter; that is coverage, not error.
        assert adapter_key_for_amc_id(amc_id_for("Canara Robeco Mutual Fund")) is None

    def test_none_and_empty_are_tolerated(self):
        assert adapter_key_for_amc_id(None) is None
        assert adapter_key_for_amc_id("") is None


class TestHealthLookup:
    def test_maps_db_amc_id_to_the_id_adapter_health_is_keyed_by(self):
        # adapter_health rows are written with the adapter's own amc_id, but the portfolio
        # tool only ever holds scheme.amc_id — this hop is what made the join work.
        assert health_lookup_ids("amc-ppfas-mutual-fund") == ["amc-ppfas", "ppfas"]

    def test_handles_an_adapter_whose_id_is_not_amc_plus_key(self):
        assert health_lookup_ids("amc-nippon-india-mutual-fund") == ["amc-nippon-india", "nippon"]

    def test_uncovered_amc_yields_no_lookup_ids(self):
        assert health_lookup_ids(amc_id_for("Canara Robeco Mutual Fund")) == []


class TestPresentAmcIds:
    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE amc (amc_id TEXT PRIMARY KEY, name TEXT)")
        return conn

    def test_returns_only_amcs_present_in_the_store(self):
        conn = self._db()
        conn.execute("INSERT INTO amc VALUES ('amc-ppfas-mutual-fund', 'PPFAS Mutual Fund')")
        assert present_amc_ids_for_adapter(conn, "ppfas") == ["amc-ppfas-mutual-fund"]
        assert present_amc_ids_for_adapter(conn, "sbi") == []

    def test_empty_store_returns_empty(self):
        # The signal for "ingest-navall has not been run", not "this AMC is unknown".
        assert present_amc_ids_for_adapter(self._db(), "ppfas") == []
