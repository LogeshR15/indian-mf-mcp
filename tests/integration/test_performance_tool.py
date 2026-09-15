from datetime import date, timedelta

from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_performance import get_fund_performance


def _seed_synthetic_fund(conn, scheme_id="scheme-1", amc_id="amc-1", plan_id="plan-1",
                          start=date(2016, 1, 1), days=2000, daily_growth=0.0004):
    repo.upsert_amc(conn, amc_id, "Test AMC")
    repo.upsert_scheme(conn, scheme_id, amc_id, "Test Flexi Cap Fund", "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", start.isoformat())
    repo.insert_taxonomy_history(conn, scheme_id, start.isoformat(), "Open Ended Schemes",
                                  "Equity Scheme", "Flexi Cap Fund", "Open Ended Schemes(Equity Scheme - Flexi Cap Fund)")
    repo.upsert_plan(conn, plan_id, scheme_id, "999999", "INF000TEST01", "Direct", "Growth",
                      None, start.isoformat())
    nav = 100.0
    rows = []
    d = start
    for _ in range(days):
        rows.append((plan_id, d.isoformat(), round(nav, 4)))
        nav *= (1 + daily_growth)
        d += timedelta(days=1)
    repo.insert_nav_points(conn, rows)
    return scheme_id


def test_get_fund_performance_end_to_end(tmp_path):
    conn = get_connection(tmp_path / "perf.db")
    scheme_id = _seed_synthetic_fund(conn)
    conn.commit()

    out = get_fund_performance(conn, [scheme_id], metrics=["trailing", "rolling", "risk", "drawdown", "stress"])
    payload = out[scheme_id]
    data = payload["data"]

    assert "cagr_1y" in data and data["cagr_1y"]["v"] is not None
    assert data["cagr_1y"]["k"] == "calculated"
    assert "cagr_since_inception" in data
    assert data["volatility_annualised"]["v"] is not None
    assert data["sharpe_ratio"]["k"] == "calculated"
    assert "rolling_returns" in data
    assert "1Y" in data["rolling_returns"]["v"]
    assert "drawdowns_gt_10pct" in data
    assert "stress_windows" in data

    # every fact must reference a resolvable source
    for fact in data.values():
        assert fact["src"] in payload["sources"]

    conn.close()


def test_missing_plan_reports_error_not_silent_default(tmp_path):
    conn = get_connection(tmp_path / "perf2.db")
    out = get_fund_performance(conn, ["nonexistent-scheme"])
    assert out["nonexistent-scheme"]["error"] == "no_plans_found"
    conn.close()


def test_requested_plan_is_actually_honoured(tmp_path):
    """plan='regular_growth' must return the Regular/Growth NAV series, not Direct/Growth.

    Regression: the plan argument used to be accepted but never consulted — every call
    silently used Direct/Growth regardless of what was requested.
    """
    conn = get_connection(tmp_path / "perf3.db")
    scheme_id = _seed_synthetic_fund(conn, plan_id="plan-direct", daily_growth=0.0004)
    repo.upsert_plan(conn, "plan-regular", scheme_id, "999998", "INF000TEST02",
                      "Regular", "Growth", None, date(2016, 1, 1).isoformat())
    nav = 100.0
    rows = []
    d = date(2016, 1, 1)
    for _ in range(2000):
        rows.append(("plan-regular", d.isoformat(), round(nav, 4)))
        nav *= (1 + 0.0002)  # deliberately different growth so the two plans diverge
        d += timedelta(days=1)
    repo.insert_nav_points(conn, rows)
    conn.commit()

    direct = get_fund_performance(conn, [scheme_id], plan="direct_growth", metrics=["trailing"])
    regular = get_fund_performance(conn, [scheme_id], plan="regular_growth", metrics=["trailing"])

    assert direct[scheme_id]["meta"]["plan_id"] == "plan-direct"
    assert regular[scheme_id]["meta"]["plan_id"] == "plan-regular"
    assert direct[scheme_id]["data"]["cagr_1y"]["v"] != regular[scheme_id]["data"]["cagr_1y"]["v"]
    conn.close()


def test_unrecognised_plan_falls_back_with_warning(tmp_path):
    conn = get_connection(tmp_path / "perf4.db")
    scheme_id = _seed_synthetic_fund(conn)
    conn.commit()

    out = get_fund_performance(conn, [scheme_id], plan="not_a_real_plan", metrics=["trailing"])
    payload = out[scheme_id]
    assert payload["meta"]["plan_id"] is not None
    assert any("not_a_real_plan" in w for w in payload["meta"]["warnings"])
    conn.close()
