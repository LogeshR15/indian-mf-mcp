"""Realised Direct-vs-Regular cost spread, measured from NAV alone (spec §3.8): regress the
daily log-return difference between the Direct and Regular plans of the same scheme. Both
series are free from AMFI, so this gives a sourced, historical cost differential without
depending on the AMFI TER page (SPA-only) or an AMC factsheet at all — and it is more
analytically honest than a single stated TER snapshot.
"""
from __future__ import annotations

import math

from indian_mf_mcp.analytics.returns import NavSeries


def _log_returns(series: NavSeries) -> dict:
    """date -> log return, keyed for alignment across two series with possibly different
    trading-day coverage."""
    out = {}
    for i in range(1, len(series)):
        prev, cur = series.navs[i - 1], series.navs[i]
        if prev > 0 and cur > 0:
            out[series.dates[i]] = math.log(cur / prev)
    return out


def realised_direct_regular_spread_bps(direct: NavSeries, regular: NavSeries) -> dict:
    """Positive bps = Direct plan outperformed Regular by that much per year, realised —
    which is the TER + distributor-commission gap actually experienced, not a stated figure.
    """
    d_rets = _log_returns(direct)
    r_rets = _log_returns(regular)
    common_dates = sorted(set(d_rets) & set(r_rets))
    if len(common_dates) < 30:
        return {"annualised_spread_bps": None, "n_common_days": len(common_dates)}

    diffs = [d_rets[dt] - r_rets[dt] for dt in common_dates]
    mean_daily_diff = sum(diffs) / len(diffs)
    annualised_diff = mean_daily_diff * 252
    return {
        "annualised_spread_bps": round(annualised_diff * 10000, 1),
        "n_common_days": len(common_dates),
        "period_start": common_dates[0].isoformat(),
        "period_end": common_dates[-1].isoformat(),
    }
