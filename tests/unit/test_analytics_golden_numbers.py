"""Hand-computed golden numbers for the analytics module.

Per-adapter parser fixtures catch a broken PDF/XLSX parse; they do NOT catch a wrong
annualisation factor (252 vs 365 vs actual/actual), a sign error in drawdown depth, or a
calendar-alignment rule silently changing. Every value asserted below was computed by hand
(or with a plain calculator) against a tiny, fully-known NAV series, to 1e-9 where the
arithmetic is exact and to a tight tolerance where it depends on `days/365.25` framing.
"""
from __future__ import annotations

from datetime import date

import pytest

from indian_mf_mcp.analytics import drawdown as dd
from indian_mf_mcp.analytics import returns as R
from indian_mf_mcp.analytics import risk as risk_mod


def series(pairs):
    dates, navs = zip(*pairs)
    return R.NavSeries(list(dates), list(navs))


# ---------------------------------------------------------------------------
# CAGR
# ---------------------------------------------------------------------------

def test_cagr_two_year_doubling_is_exactly_sqrt2_minus_1():
    # NAV doubles over exactly 2 calendar years (730 days, close enough to 2*365.25 for the
    # hand-check): CAGR = 2^(1/2) - 1 = 0.41421356...
    s = series([
        (date(2020, 1, 1), 100.0),
        (date(2022, 1, 1), 200.0),
    ])
    days = (date(2022, 1, 1) - date(2020, 1, 1)).days  # 731 (2020 is a leap year)
    years = days / 365.25
    expected = 2.0 ** (1 / years) - 1.0
    got = R.cagr(s, date(2020, 1, 1), date(2022, 1, 1))
    assert got == pytest.approx(expected, abs=1e-9)
    assert got == pytest.approx(0.41388, abs=1e-5)  # explicit hand-computed value (731 days)


def test_cagr_sub_year_horizon_is_simple_return_not_annualised():
    # A 6-month, +10% move must be reported as +10%, never annualised to ~21%.
    s = series([
        (date(2024, 1, 1), 100.0),
        (date(2024, 7, 1), 110.0),
    ])
    got = R.cagr(s, date(2024, 1, 1), date(2024, 7, 1))
    assert got == pytest.approx(0.10, abs=1e-9)


def test_since_inception_cagr_matches_manual_cagr_over_full_series():
    s = series([
        (date(2016, 1, 1), 10.0),
        (date(2020, 1, 1), 15.0),
        (date(2026, 1, 1), 40.0),
    ])
    years = (date(2026, 1, 1) - date(2016, 1, 1)).days / 365.25
    expected = (40.0 / 10.0) ** (1 / years) - 1.0
    assert R.since_inception_cagr(s) == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# Calendar alignment (spec: "N years ago" with no exact NAV) — the rule is
# "last available NAV on or before the target date", stated explicitly here so it can't
# silently drift, and must behave identically for a fund and a benchmark proxy series
# since both route through the same `cagr()`/`value_on_or_before()`.
# ---------------------------------------------------------------------------

def test_calendar_gap_uses_last_available_nav_on_or_before_target():
    # Deliberately gappy: no NAV on the exact 3-years-back date (a holiday/weekend/AMC
    # filing gap) — the rule must fall back to the latest prior NAV, never interpolate and
    # never skip forward to the next available date.
    s = series([
        (date(2021, 1, 4), 50.0),   # last trading day before the target gap
        (date(2021, 1, 8), 52.0),   # first trading day AFTER the target (must be ignored)
        (date(2024, 1, 5), 100.0),
    ])
    target = date(2021, 1, 6)  # a weekend with no NAV point
    p0 = s.value_on_or_before(target)
    assert p0 == (date(2021, 1, 4), 50.0)  # not (2021-01-08, 52.0)

    got = R.cagr(s, target, date(2024, 1, 5))
    days = (date(2024, 1, 5) - date(2021, 1, 4)).days
    expected = (100.0 / 50.0) ** (1 / (days / 365.25)) - 1.0
    assert got == pytest.approx(expected, abs=1e-9)


def test_value_on_or_before_returns_none_when_target_precedes_all_data():
    s = series([(date(2020, 1, 1), 100.0)])
    assert s.value_on_or_before(date(2019, 1, 1)) is None


# ---------------------------------------------------------------------------
# Risk: Sharpe / Sortino (annualisation factor = 252 trading days, verified by hand)
# ---------------------------------------------------------------------------

def test_sharpe_ratio_on_constant_positive_daily_return():
    # A constant +0.05%/day return has zero volatility around the mean but the
    # population-style variance formula here uses N-1 with N identical values -> var=0,
    # so Sharpe is undefined (division by zero) — must return None, never a fabricated
    # infinite/zero value.
    daily = [0.0005] * 30
    assert risk_mod.sharpe_ratio(daily, risk_free_annual=0.065) is None


def test_sharpe_ratio_hand_computed_two_point_series():
    # Two daily returns: +1%, -1%. mean=0, var=(0.01^2+0.01^2)/(2-1)=0.0002,
    # vol=sqrt(0.0002)*sqrt(252). ann_ret=(1+0)^252-1=0.
    daily = [0.01, -0.01]
    expected_vol = ((0.0002) ** 0.5) * (252 ** 0.5)
    expected_sharpe = (0.0 - 0.065) / expected_vol
    assert risk_mod.annualised_volatility(daily) == pytest.approx(expected_vol, abs=1e-9)
    assert risk_mod.sharpe_ratio(daily, risk_free_annual=0.065) == pytest.approx(expected_sharpe, abs=1e-9)


def test_sortino_ignores_upside_deviation():
    # Sortino's downside deviation only counts negative returns; an all-positive series
    # must therefore report a HIGHER (or equal) risk-adjusted figure than Sharpe on the
    # same series once volatility includes the upside noise.
    daily = [0.01, 0.02, 0.015, 0.005, 0.01]
    sortino = risk_mod.sortino_ratio(daily, risk_free_annual=0.0)
    # no negative days -> downside_dev is 0 -> Sortino is undefined, must be None (not inf)
    assert sortino is None


# ---------------------------------------------------------------------------
# Drawdown: sign, depth, peak/trough/recovery dates on a fully-known V-shaped series
# ---------------------------------------------------------------------------

def test_drawdown_depth_sign_and_recovery_on_known_v_shape():
    s = series([
        (date(2020, 1, 1), 100.0),
        (date(2020, 2, 1), 120.0),   # peak
        (date(2020, 3, 1), 90.0),    # trough: (90/120)-1 = -0.25
        (date(2020, 4, 1), 130.0),   # recovers above peak
    ])
    ddowns = dd.find_drawdowns(s, min_depth_pct=0.10)
    assert len(ddowns) == 1
    d = ddowns[0]
    assert d.peak_date == date(2020, 2, 1)
    assert d.trough_date == date(2020, 3, 1)
    assert d.depth_pct == pytest.approx(-0.25, abs=1e-9)  # must be negative, exactly -25%
    assert d.recovery_date == date(2020, 4, 1)
    assert d.duration_days == (date(2020, 3, 1) - date(2020, 2, 1)).days
    assert d.recovery_days == (date(2020, 4, 1) - date(2020, 3, 1)).days


def test_drawdown_below_threshold_is_not_reported():
    # A -5% dip must not appear when min_depth_pct=0.10.
    s = series([
        (date(2020, 1, 1), 100.0),
        (date(2020, 2, 1), 100.0),
        (date(2020, 3, 1), 95.0),
        (date(2020, 4, 1), 105.0),
    ])
    assert dd.find_drawdowns(s, min_depth_pct=0.10) == []


def test_unresolved_drawdown_at_series_end_has_no_recovery():
    s = series([
        (date(2020, 1, 1), 100.0),
        (date(2020, 6, 1), 60.0),   # -40%, never recovers within the series
    ])
    ddowns = dd.find_drawdowns(s, min_depth_pct=0.10)
    assert len(ddowns) == 1
    assert ddowns[0].recovery_date is None
    assert ddowns[0].recovery_days is None
