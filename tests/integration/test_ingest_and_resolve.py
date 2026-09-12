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


FIXTURE_V2 = Path(__file__).parent.parent / "fixtures" / "sample_navall_v2.txt"


def test_ingest_current_layout_stores_navs_and_plan_metadata(tmp_path):
    conn = get_connection(tmp_path / "test_v2.db")
    stats = run_daily_ingest(conn, raw=FIXTURE_V2.read_bytes(), as_of=date(2026, 9, 10))

    assert stats["plans"] == 6
    assert stats["nav_points"] == 6  # regression: the 8-column layout used to yield 0
    assert stats["warnings"] == []

    candidates = resolve_fund(conn, "Parag Parikh Flexi Cap", limit=5)["Parag Parikh Flexi Cap"]
    plans = {p["amfi_scheme_code"]: p for p in candidates[0]["plans"]}
    assert plans["122639"]["plan_type"] == "Direct"
    assert plans["122639"]["option_type"] == "Growth"
    assert plans["122640"]["plan_type"] == "Regular"
    assert plans["122640"]["option_type"] == "IDCW"
    assert plans["122640"]["idcw_variant"] == "Payout"
    conn.close()


def test_scheme_name_with_option_token_is_not_stripped(tmp_path):
    """'Nippon India Growth Fund' must not become 'Nippon India Fund'."""
    conn = get_connection(tmp_path / "test_v2_name.db")
    run_daily_ingest(conn, raw=FIXTURE_V2.read_bytes(), as_of=date(2026, 9, 10))

    names = [r[0] for r in conn.execute("select name from scheme").fetchall()]
    assert "Nippon India Growth Fund" in names
    conn.close()


def test_layout_change_surfaces_a_warning(tmp_path):
    """A file whose NAV column stops parsing must not report a silent success."""
    conn = get_connection(tmp_path / "test_broken.db")
    broken = FIXTURE_V2.read_text().replace(";75.1234;", ";NAV;").replace(";69.4321;", ";NAV;")
    broken = "\n".join(
        ln if not ln[:1].isdigit() else ";".join(ln.split(";")[:-2] + ["NAV", "Date"])
        for ln in broken.splitlines()
    )
    stats = run_daily_ingest(conn, raw=broken.encode(), as_of=date(2026, 9, 10))

    assert stats["plans"] == 6
    assert stats["nav_points"] == 0
    assert stats["warnings"] and "0 NAV points" in stats["warnings"][0]
    conn.close()
