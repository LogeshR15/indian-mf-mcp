"""Drawdown detection: peak, trough, depth, duration, recovery. Deterministic, from NAV series."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from indian_mf_mcp.analytics.returns import NavSeries


@dataclass
class Drawdown:
    peak_date: date
    peak_nav: float
    trough_date: date
    trough_nav: float
    depth_pct: float          # negative number, e.g. -0.23
    recovery_date: date | None
    duration_days: int        # peak -> trough
    recovery_days: int | None  # trough -> recovery (None if not yet recovered)


def find_drawdowns(series: NavSeries, min_depth_pct: float = 0.10) -> list[Drawdown]:
    if len(series) < 2:
        return []
    drawdowns: list[Drawdown] = []
    peak_idx = 0
    trough_idx = 0
    i = 1
    n = len(series)
    while i < n:
        if series.navs[i] > series.navs[peak_idx]:
            # potential drawdown ended (recovered) — close it out if depth qualifies
            depth = (series.navs[trough_idx] / series.navs[peak_idx]) - 1.0
            if trough_idx > peak_idx and abs(depth) >= min_depth_pct:
                drawdowns.append(Drawdown(
                    peak_date=series.dates[peak_idx], peak_nav=series.navs[peak_idx],
                    trough_date=series.dates[trough_idx], trough_nav=series.navs[trough_idx],
                    depth_pct=depth,
                    recovery_date=series.dates[i],
                    duration_days=(series.dates[trough_idx] - series.dates[peak_idx]).days,
                    recovery_days=(series.dates[i] - series.dates[trough_idx]).days,
                ))
            peak_idx = i
            trough_idx = i
        elif series.navs[i] < series.navs[trough_idx]:
            trough_idx = i
        i += 1

    # unresolved drawdown at series end (not yet recovered)
    if trough_idx > peak_idx:
        depth = (series.navs[trough_idx] / series.navs[peak_idx]) - 1.0
        if abs(depth) >= min_depth_pct:
            drawdowns.append(Drawdown(
                peak_date=series.dates[peak_idx], peak_nav=series.navs[peak_idx],
                trough_date=series.dates[trough_idx], trough_nav=series.navs[trough_idx],
                depth_pct=depth, recovery_date=None,
                duration_days=(series.dates[trough_idx] - series.dates[peak_idx]).days,
                recovery_days=None,
            ))
    return drawdowns


# Named stress windows — dates are approximate calendar bounds, stated explicitly in output
# so Claude/user can see exactly what window was used, never silently redefined.
STRESS_WINDOWS = {
    "covid_crash_2020": (date(2020, 2, 20), date(2020, 3, 23)),
    "drawdown_2022": (date(2022, 1, 1), date(2022, 6, 30)),
}


def stress_window_return(series: NavSeries, start: date, end: date) -> float | None:
    p0 = series.value_on_or_before(start)
    p1 = series.value_on_or_before(end)
    if not p0 or not p1 or p0[1] <= 0:
        return None
    return (p1[1] / p0[1]) - 1.0
