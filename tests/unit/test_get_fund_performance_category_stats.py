"""Regression test: _category_stats() must exclude segregated (side-pocketed) portfolios from
category peer comparisons (spec §9.2: "detect and surface; don't merge") — a side-pocket's
return series reflects a defaulted-exposure workout, not ordinary fund-manager performance, so
averaging it into a category median would silently distort the comparator for every other fund.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.tools.get_fund_performance import _category_stats


def _in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        conn.executescript(f.read())
    return conn


def _seed_scheme(conn, scheme_id, name, category, start, nav_growth_daily=1.0002, days=400):
    amc_id = f"amc-{scheme_id}"
    repo.upsert_amc(conn, amc_id, "Test AMC")
    repo.upsert_scheme(conn, scheme_id, amc_id, name, "Open Ended Schemes",
                        category, "Credit Risk Fund", start.isoformat())
    plan_id = f"plan-{scheme_id}"
    repo.upsert_plan(conn, plan_id, scheme_id, f"code-{scheme_id}", None,
                      "Direct", "Growth", None, start.isoformat())

    nav = 100.0
    rows = []
    d = start
    for _ in range(days):
        rows.append((plan_id, d.isoformat(), round(nav, 4)))
        nav *= nav_growth_daily
        d += timedelta(days=1)
    repo.insert_nav_points(conn, rows)
    conn.commit()


def test_segregated_portfolio_excluded_from_category_stats():
    conn = _in_memory_db()
    start = date(2024, 1, 1)
    as_of = date(2026, 8, 1)

    _seed_scheme(conn, "normal-1", "Test Credit Risk Fund", "Debt Scheme", start)
    _seed_scheme(conn, "normal-2", "Another Credit Risk Fund", "Debt Scheme", start)
    _seed_scheme(
        conn, "segregated-1",
        "Test Credit Risk Fund - Segregated Portfolio 1", "Debt Scheme", start,
        nav_growth_daily=1.0,  # frozen NAV, typical of a side-pocket workout
    )

    stats = _category_stats(conn, "normal-1", "Debt Scheme", 2.0, as_of)
    assert stats["n_funds"] == 2  # the segregated portfolio must not be counted


def test_without_exclusion_would_have_counted_three():
    """Sanity check on the test fixture itself: confirms all three schemes really do have
    enough NAV history to be picked up by the underlying query, so the n_funds==2 assertion
    above is actually exercising the exclusion and not just missing data."""
    conn = _in_memory_db()
    start = date(2024, 1, 1)
    as_of = date(2026, 8, 1)

    _seed_scheme(conn, "normal-1", "Test Credit Risk Fund", "Debt Scheme", start)
    _seed_scheme(conn, "normal-2", "Another Credit Risk Fund", "Debt Scheme", start)
    _seed_scheme(
        conn, "segregated-1",
        "Test Credit Risk Fund - Segregated Portfolio 1", "Debt Scheme", start,
    )

    rows = conn.execute(
        """SELECT COUNT(*) AS n FROM plan p JOIN scheme s ON s.scheme_id = p.scheme_id
           WHERE s.category = 'Debt Scheme' AND p.plan_type = 'Direct'
             AND p.option_type = 'Growth' AND p.active = 1"""
    ).fetchone()
    assert rows["n"] == 3


def test_caveat_mentions_segregated_portfolio_exclusion():
    conn = _in_memory_db()
    start = date(2024, 1, 1)
    as_of = date(2026, 8, 1)
    _seed_scheme(conn, "normal-1", "Test Credit Risk Fund", "Debt Scheme", start)

    stats = _category_stats(conn, "normal-1", "Debt Scheme", 2.0, as_of)
    assert "segregated" in stats["survivorship_bias_caveat"].lower()
