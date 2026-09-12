from datetime import date, timedelta

from indian_mf_mcp.analytics.cost import realised_direct_regular_spread_bps
from indian_mf_mcp.analytics.returns import NavSeries


def _series(start_nav, daily_growth, days, start_date=date(2020, 1, 1)):
    dates, navs = [], []
    nav = start_nav
    d = start_date
    for _ in range(days):
        dates.append(d)
        navs.append(nav)
        nav *= (1 + daily_growth)
        d += timedelta(days=1)
    return NavSeries(dates, navs)


def test_direct_outperforms_regular_by_expected_bps():
    # Direct grows ~50bps/yr faster than Regular (typical TER differential)
    daily_direct = (1.005) ** (1 / 252) - 1
    daily_regular = 0.0
    direct = _series(100.0, daily_direct, 300)
    regular = _series(100.0, daily_regular, 300)
    result = realised_direct_regular_spread_bps(direct, regular)
    assert result["annualised_spread_bps"] is not None
    assert 40 < result["annualised_spread_bps"] < 60


def test_insufficient_overlap_reports_none_not_a_guess():
    direct = _series(100.0, 0.0005, 5)
    regular = _series(100.0, 0.0, 5)
    result = realised_direct_regular_spread_bps(direct, regular)
    assert result["annualised_spread_bps"] is None
    assert result["n_common_days"] < 30
