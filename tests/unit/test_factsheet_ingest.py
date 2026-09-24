"""ingest_factsheet / backfill_factsheets behaviour on a tmp store, driven by a real factsheet
page (PPFAS Flexi Cap, Aug 2026 edition): page scoping, what is stored, and when a change
event is (and isn't) emitted."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef
from indian_mf_mcp.ingest.factsheet_ingest import backfill_factsheets, ingest_factsheet
from indian_mf_mcp.store import manager_repository as mrep
from indian_mf_mcp.store.db import get_connection

PPFAS = Path(__file__).parent.parent / "fixtures" / "factsheets" / "ppfas_flexicap_2026.pdf"
HINT = "Parag Parikh Flexi Cap Fund"
URL = "https://amc.ppfas.com/downloads/factsheet/2026/ppfas-mf-factsheet-for-August-2026.pdf"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BLOB_DIR", tmp_path / "blobs")
    c = get_connection(tmp_path / "test.db")
    c.execute("INSERT INTO amc (amc_id, name) VALUES ('amc-p', 'PPFAS Mutual Fund')")
    for sid, name in (("s-flexi", HINT), ("s-liquid", "Parag Parikh Liquid Fund")):
        c.execute("INSERT INTO scheme (scheme_id, amc_id, name, active) VALUES (?, 'amc-p', ?, 1)",
                  (sid, name))
        for plan in ("Direct", "Regular"):
            c.execute("INSERT INTO plan (plan_id, scheme_id, amfi_scheme_code, plan_type, "
                      "option_type, active) VALUES (?, ?, ?, ?, 'Growth', 1)",
                      (f"{sid}-{plan}", sid, f"{sid}{plan}", plan))
    c.commit()
    yield c
    c.close()


def _ingest(conn, doc_date="2026-08-31", hint=HINT, scheme_id="s-flexi"):
    return ingest_factsheet(conn, PPFAS.read_bytes(), URL, scheme_id, doc_date=doc_date,
                            scheme_hint=hint)


def test_first_factsheet_is_a_baseline_not_a_change(conn):
    st = _ingest(conn)
    assert st["pages"] == [1]
    assert st["managers_found"] == 7
    assert st["change_events"] == 0
    assert conn.execute("SELECT COUNT(*) FROM change_event").fetchone()[0] == 0
    names = {r["name_normalised"] for r in mrep.get_current_managers_for_scheme(conn, "s-flexi")}
    assert "Rajeev Thakkar" in names and len(names) == 7


def test_since_inception_resolves_to_printed_inception_date(conn):
    _ingest(conn)
    rows = {r["name_normalised"]: r["from_date"]
            for r in mrep.get_current_managers_for_scheme(conn, "s-flexi")}
    assert rows["Rajeev Thakkar"] == "2013-05-24"
    assert rows["Mansi Kariya"] == "2023-12-22"


def test_base_expense_ratio_is_never_stored_as_ter(conn):
    _ingest(conn)
    assert conn.execute("SELECT COUNT(*) FROM ter_history").fetchone()[0] == 0
    row = conn.execute("SELECT ter_json FROM factsheet_extract WHERE scheme_id='s-flexi'").fetchone()
    assert json.loads(row["ter_json"]) == {"BER": {"Direct": 0.53, "Regular": 1.05}}


def test_manager_change_against_previous_month(conn):
    conn.execute(
        "INSERT INTO factsheet_extract (scheme_id, doc_id, doc_date, managers_json, ter_json) "
        "VALUES ('s-flexi', NULL, '2026-07-31', ?, '{}')",
        (json.dumps([{"name": "Rajeev Thakkar", "managing_since": None},
                     {"name": "Someone Departed", "managing_since": None}]),),
    )
    st = _ingest(conn)
    assert st["change_events"] == 1
    ev = conn.execute("SELECT * FROM change_event").fetchone()
    assert ev["event_type"] == "manager_change"
    assert ev["effective_date"] == "2026-08-31"
    assert "Someone Departed" in json.loads(ev["before_json"])["managers"]


def test_older_month_does_not_rewind_current_state(conn):
    _ingest(conn, doc_date="2026-08-31")
    st = _ingest(conn, doc_date="2026-06-30")
    assert any("newer factsheet" in w for w in st["warnings"])
    dates = [r[0] for r in conn.execute(
        "SELECT doc_date FROM factsheet_extract WHERE scheme_id='s-flexi' ORDER BY doc_date")]
    assert dates == ["2026-06-30", "2026-08-31"]
    assert conn.execute("SELECT COUNT(*) FROM change_event").fetchone()[0] == 0


def test_scheme_not_in_the_pdf_gets_nothing(conn):
    st = _ingest(conn, hint="Parag Parikh Liquid Fund", scheme_id="s-liquid")
    assert st["pages"] == [] and st["managers_found"] == 0
    assert any("no page of this factsheet" in w for w in st["warnings"])
    assert conn.execute(
        "SELECT COUNT(*) FROM factsheet_extract WHERE scheme_id='s-liquid'").fetchone()[0] == 0
    assert mrep.get_current_managers_for_scheme(conn, "s-liquid") == []


def test_reingest_stores_pages_once(conn):
    _ingest(conn)
    _ingest(conn)
    assert conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM document_section").fetchone()[0] == 2


class _CombinedAdapter:
    factsheet_scope = "combined"

    def __init__(self):
        self.fetches = 0

    def list_documents(self, doc_type, since, scheme_hint=None, client=None):
        assert doc_type == DocType.FACTSHEET
        return [DocumentRef(url=URL, doc_type=DocType.FACTSHEET, as_of_date=date(2026, 8, 31))]

    def fetch(self, ref, client=None):
        self.fetches += 1
        return PPFAS.read_bytes()


def test_combined_backfill_fetches_each_month_once_for_all_schemes(conn):
    adapter = _CombinedAdapter()
    totals = backfill_factsheets(conn, adapter, [("s-flexi", HINT),
                                                 ("s-liquid", "Parag Parikh Liquid Fund")],
                                 date(2026, 8, 1))
    assert adapter.fetches == 1
    assert totals["documents"] == 1 and totals["extracted"] == 1 and totals["no_pages"] == 1


def test_manager_already_managing_before_previous_month_is_not_an_appointment(conn):
    """The previous month's read missed some managers (different layout); their managing-since
    predates it, so seeing them now is not a change."""
    conn.execute(
        "INSERT INTO factsheet_extract (scheme_id, doc_id, doc_date, managers_json, ter_json) "
        "VALUES ('s-flexi', NULL, '2026-07-31', ?, '{}')",
        (json.dumps([{"name": n, "managing_since": None} for n in
                     ("Rajeev Thakkar", "Raunak Onkar", "Raj Mehta", "Rukun Tarachandani",
                      "Mansi Kariya")]),),
    )
    st = _ingest(conn)  # Aug adds Tejas Soman + Aishwarya Dhar, both "since 2025-09-01"
    assert st["change_events"] == 0


def test_spelling_correction_is_not_a_manager_change():
    from indian_mf_mcp.ingest.factsheet_ingest import _same_person
    assert _same_person("Ranjana Gupta", "Ranjhana Gupta")
    assert not _same_person("Ranjana Gupta", "Anjali Gupta")
    assert not _same_person("Amit Ganatra", "Amit Sinha")


def test_spelling_fix_of_a_long_standing_manager_emits_nothing(conn):
    """Old spelling in the previous month; the new spelling carries a managing-since that
    predates it. Neither an appointment nor a departure."""
    conn.execute(
        "INSERT INTO factsheet_extract (scheme_id, doc_id, doc_date, managers_json, ter_json) "
        "VALUES ('s-flexi', NULL, '2026-07-31', ?, '{}')",
        (json.dumps([{"name": n, "managing_since": None} for n in
                     ("Rajeev Thakkar", "Raunak Onkar", "Raj Mehta", "Rukun Tarachandani",
                      "Mansi Kariyaa", "Tejas Soman", "Aishwarya Dhar")]),),
    )
    assert _ingest(conn)["change_events"] == 0  # Aug prints "Mansi Kariya", since 2023-12-22
