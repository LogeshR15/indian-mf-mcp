from datetime import date
from pathlib import Path

from indian_mf_mcp.ingest.amfi_navall import run_daily_ingest
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.resolve_fund import resolve_fund

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_navall.txt"


def test_ingest_then_resolve_by_name_and_isin(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    raw = FIXTURE.read_bytes()
    stats = run_daily_ingest(conn, raw=raw, as_of=date(2026, 9, 10))
    assert stats["plans"] == 5
    assert stats["schemes"] == 3

    by_name = resolve_fund(conn, "Parag Parikh Flexi Cap", limit=5)
    candidates = by_name["Parag Parikh Flexi Cap"]
    assert len(candidates) == 1
    assert candidates[0]["amc"] == "Parag Parikh Financial Advisory Services Private Limited"
    assert len(candidates[0]["plans"]) == 2

    by_isin = resolve_fund(conn, "INF879O01027", limit=5)
    assert by_isin["INF879O01027"][0]["scheme_id"] == candidates[0]["scheme_id"]

    no_match = resolve_fund(conn, "Totally Unknown Fund XYZ", limit=5)
    assert no_match["Totally Unknown Fund XYZ"] == []
    assert "_warnings" in no_match
    conn.close()
