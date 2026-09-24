"""Regressions for the 2026-09-24 live review: wrong benchmark proxy codes, category peers
taken from the top-level category, `period` ignored, positional (not date) alignment of fund
vs benchmark returns, TREPS deal codes shown raw, and renamed/hyphenated names not resolving.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from indian_mf_mcp.analytics.benchmark import (
    BENCHMARK_PROXIES, aligned_daily_returns, resolve_benchmark_proxy,
)
from indian_mf_mcp.analytics.returns import NavSeries
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.tools.get_fund_performance import _category_stats, _parse_period, get_fund_performance
from indian_mf_mcp.tools.get_fund_portfolio import _display_name
from indian_mf_mcp.tools.resolve_fund import resolve_fund


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        conn.executescript(f.read())
    return conn


def _seed(conn, sid, name, code, category="Equity Scheme", sub="Flexi Cap Fund",
          isin=None, plan_type="Direct", option="Growth", start=date(2020, 1, 1),
          days=900, growth=1.0003, skip_dates=()):
    repo.upsert_amc(conn, "amc-t", "Test AMC")
    repo.upsert_scheme(conn, sid, "amc-t", name, "Open Ended Schemes", category, sub,
                       start.isoformat())
    pid = f"plan-{code}"
    repo.upsert_plan(conn, pid, sid, code, isin, plan_type, option, None, start.isoformat())
    nav, rows, d = 100.0, [], start
    for _ in range(days):
        if d not in skip_dates:
            rows.append((pid, d.isoformat(), round(nav, 6)))
        nav *= growth
        d += timedelta(days=1)
    repo.insert_nav_points(conn, rows)
    conn.commit()
    return pid


# --- benchmark proxy registry ------------------------------------------------------------

def test_registry_entries_pin_isin_and_name():
    for key, e in BENCHMARK_PROXIES.items():
        assert e.get("isin"), key
        assert e.get("name_must_contain"), key


def test_nasdaq_code_no_longer_used_for_nifty_500():
    assert BENCHMARK_PROXIES["nifty 500 tri"]["scheme_code"] != "145552"


def test_proxy_refused_when_store_fund_does_not_match_registry():
    conn = _db()
    e = BENCHMARK_PROXIES["nifty 500 tri"]
    _seed(conn, "s-wrong", "Motilal Oswal Nasdaq 100 Fund of Fund", e["scheme_code"], isin=e["isin"])
    proxy = resolve_benchmark_proxy(conn, "Nifty 500 TRI")
    assert proxy["series"] is None
    assert "registry_mismatch" in proxy


def test_proxy_accepted_when_it_matches():
    conn = _db()
    e = BENCHMARK_PROXIES["nifty 500 tri"]
    _seed(conn, "s-ok", "Motilal Oswal Nifty 500 Index Fund", e["scheme_code"], isin=e["isin"])
    proxy = resolve_benchmark_proxy(conn, "Nifty 500 TRI")
    assert proxy["series"] is not None and "registry_mismatch" not in proxy


# --- date alignment -------------------------------------------------------------------------

def test_aligned_returns_pair_by_date_not_position():
    d = [date(2026, 1, i) for i in range(1, 6)]
    fund = NavSeries(d, [100, 101, 102, 103, 104])
    bench = NavSeries([d[0], d[1], d[3], d[4]], [10, 11, 13, 14])  # d[2] missing
    f, b = aligned_daily_returns(fund, bench)
    assert len(f) == len(b) == 3
    # The pair spanning the gap is d1 -> d3 for BOTH series.
    assert abs(f[1] - (103 / 101 - 1)) < 1e-12
    assert abs(b[1] - (13 / 11 - 1)) < 1e-12


# --- category peers --------------------------------------------------------------------------

def test_category_stats_restricted_to_sub_category():
    conn = _db()
    _seed(conn, "flexi-1", "A Flexi Cap Fund", "1", sub="Flexi Cap Fund")
    _seed(conn, "flexi-2", "B Flexi Cap Fund", "2", sub="Flexi Cap Fund")
    _seed(conn, "small-1", "C Small Cap Fund", "3", sub="Small Cap Fund")
    as_of = date(2022, 6, 1)
    assert _category_stats(conn, "flexi-1", "Equity Scheme", 2.0, as_of)["n_funds"] == 3
    assert _category_stats(conn, "flexi-1", "Equity Scheme", 2.0, as_of,
                           sub_category="Flexi Cap Fund")["n_funds"] == 2


# --- period -------------------------------------------------------------------------------

def test_parse_period():
    assert _parse_period("5Y") == (5.0, None)
    assert _parse_period("since_inception") == (None, None)
    years, warn = _parse_period("bogus")
    assert years == 10.0 and warn


def test_period_bounds_nav_series_and_horizons():
    conn = _db()
    today = date.today()
    _seed(conn, "s1", "A Flexi Cap Fund", "1", start=today - timedelta(days=4 * 365), days=4 * 365)
    out = get_fund_performance(conn, ["s1"], period="1Y", metrics=["trailing"],
                               comparators=[], nav_series=True, provenance="full")["s1"]
    data = out["data"]
    assert "cagr_1y" in data and "cagr_3y" not in data
    first = date.fromisoformat(data["nav_series"]["v"][0]["date"])
    assert first >= today - timedelta(days=367)


# --- portfolio display ------------------------------------------------------------------------

def test_treps_code_gets_readable_name():
    assert _display_name({"instrument_name": "TRP_010926", "asset_class": "cash"}) \
        == "TREPS / Reverse Repo (TRP_010926)"
    assert _display_name({"instrument_name": "HDFC Bank Limited", "asset_class": "equity"}) \
        == "HDFC Bank Limited"


# --- resolve ----------------------------------------------------------------------------------

def test_resolve_hyphen_rename_and_word_order():
    conn = _db()
    _seed(conn, "hdfc-mid", "HDFC Mid Cap Fund", "118989", sub="Mid Cap Fund")
    _seed(conn, "hdfc-lm", "HDFC Large & Mid Cap Fund", "130498", sub="Large & Mid Cap Fund")
    _seed(conn, "sbi-lc", "SBI Large Cap Fund", "119598", sub="Large Cap Fund")
    _seed(conn, "nip-50", "Nippon India Index Fund - Nifty 50 Plan", "118741",
          category="Other Scheme", sub="Index Funds")
    r = resolve_fund(conn, ["HDFC Mid-Cap Opportunities", "SBI Bluechip",
                            "Nippon India Nifty 50 Index", "HDFC Midcap"])
    assert r["HDFC Mid-Cap Opportunities"][0]["canonical_name"] == "HDFC Mid Cap Fund"
    assert r["SBI Bluechip"][0]["canonical_name"] == "SBI Large Cap Fund"
    assert r["Nippon India Nifty 50 Index"][0]["canonical_name"].startswith("Nippon India Index Fund")
    assert r["HDFC Midcap"][0]["canonical_name"] == "HDFC Mid Cap Fund"
    assert r["SBI Bluechip"][0]["matched_via"].startswith("former_name")
