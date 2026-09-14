from datetime import date, timedelta

from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_profile import get_fund_profile


def _seed(conn, scheme_id="scheme-1", amc_id="amc-1", start=date(2016, 1, 1), days=500):
    repo.upsert_amc(conn, amc_id, "Test AMC")
    repo.upsert_scheme(conn, scheme_id, amc_id, "Test Flexi Cap Fund", "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", start.isoformat())
    direct_id, regular_id = "plan-direct", "plan-regular"
    repo.upsert_plan(conn, direct_id, scheme_id, "1001", "INF000D01", "Direct", "Growth", None, start.isoformat())
    repo.upsert_plan(conn, regular_id, scheme_id, "1002", "INF000R01", "Regular", "Growth", None, start.isoformat())

    d_rows, r_rows = [], []
    d_nav, r_nav = 100.0, 100.0
    d = start
    for _ in range(days):
        d_rows.append((direct_id, d.isoformat(), round(d_nav, 4)))
        r_rows.append((regular_id, d.isoformat(), round(r_nav, 4)))
        d_nav *= 1.00025
        r_nav *= 1.00015
        d += timedelta(days=1)
    repo.insert_nav_points(conn, d_rows)
    repo.insert_nav_points(conn, r_rows)
    conn.commit()
    return scheme_id


def test_identity_and_costs_present_managers_and_ter_honestly_unavailable(tmp_path):
    conn = get_connection(tmp_path / "profile.db")
    scheme_id = _seed(conn)

    out = get_fund_profile(conn, [scheme_id])
    data = out[scheme_id]["data"]

    assert data["name"]["v"] == "Test Flexi Cap Fund"
    assert data["category"]["v"] == "Equity Scheme"
    assert len(data["plans"]["v"]) == 2

    # TER not implemented -> explicitly None with a caveat, never fabricated
    assert data["ter_direct"]["v"] is None
    assert "caveat" in data["ter_direct"]

    # realised spread IS computable from NAV alone and should be present
    assert data["realised_direct_regular_spread_bps"]["v"] is not None
    assert data["realised_direct_regular_spread_bps"]["k"] == "calculated"

    meta = out[scheme_id]["meta"]
    assert any("manager" in w.lower() for w in meta.get("warnings", []))

    conn.close()


def test_unknown_scheme_reports_error_not_empty_profile(tmp_path):
    conn = get_connection(tmp_path / "profile2.db")
    out = get_fund_profile(conn, ["nonexistent"])
    assert out["nonexistent"]["error"] == "scheme_not_found"
    conn.close()
