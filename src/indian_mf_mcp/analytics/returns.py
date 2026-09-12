"""Deterministic return computations over a NAV series. Same methodology for every fund —
the boundary rule from the spec: if two analysts would compute the same number, it's here."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class NavSeries:
    dates: list[date]
    navs: list[float]

    def __len__(self):
        return len(self.dates)

    def slice(self, start: date | None, end: date | None) -> "NavSeries":
        d, n = [], []
        for dt, v in zip(self.dates, self.navs):
            if start and dt < start:
                continue
            if end and dt > end:
                continue
            d.append(dt)
            n.append(v)
        return NavSeries(d, n)

    def value_on_or_before(self, target: date) -> tuple[date, float] | None:
        best = None
        for dt, v in zip(self.dates, self.navs):
            if dt <= target:
                best = (dt, v)
            else:
                break
        return best


def from_rows(rows) -> NavSeries:
    dates = [datetime.strptime(r["date"], "%Y-%m-%d").date() for r in rows]
    navs = [float(r["nav"]) for r in rows]
    return NavSeries(dates, navs)


def point_to_point_return(series: NavSeries, start: date, end: date) -> float | None:
    p0 = series.value_on_or_before(start)
    p1 = series.value_on_or_before(end)
    if not p0 or not p1 or p0[1] <= 0:
        return None
    return (p1[1] / p0[1]) - 1.0


def cagr(series: NavSeries, start: date, end: date) -> float | None:
    p0 = series.value_on_or_before(start)
    p1 = series.value_on_or_before(end)
    if not p0 or not p1 or p0[1] <= 0:
        return None
    days = (p1[0] - p0[0]).days
    if days <= 0:
        return None
    years = days / 365.25
    if years < 1:
        # Sub-year horizon: report simple point-to-point, not annualised (avoids false precision
        # from annualising a tiny window).
        return (p1[1] / p0[1]) - 1.0
    return (p1[1] / p0[1]) ** (1 / years) - 1.0


def trailing_cagr_ladder(series: NavSeries, as_of: date, horizons_years: list[float]) -> dict:
    out = {}
    for h in horizons_years:
        start = as_of.replace(year=as_of.year - int(h)) if h == int(h) else as_of
        if h != int(h):
            days = int(h * 365.25)
            start = as_of.fromordinal(as_of.toordinal() - days)
        val = cagr(series, start, as_of)
        out[f"{h}Y"] = val
    return out


def since_inception_cagr(series: NavSeries) -> float | None:
    if len(series) < 2:
        return None
    return cagr(series, series.dates[0], series.dates[-1])


def daily_returns(series: NavSeries) -> list[float]:
    out = []
    for i in range(1, len(series)):
        prev, cur = series.navs[i - 1], series.navs[i]
        if prev > 0:
            out.append((cur / prev) - 1.0)
    return out


def rolling_returns(series: NavSeries, window_years: float) -> list[float]:
    """Rolling point-to-point return over a fixed calendar window, sampled daily."""
    window_days = int(window_years * 365.25)
    results = []
    n = len(series)
    j = 0
    for i in range(n):
        target_date = series.dates[i]
        start_target = target_date.fromordinal(target_date.toordinal() - window_days)
        # find the nav on/immediately after start_target using forward pointer
        while j < i and series.dates[j] < start_target:
            j += 1
        if series.dates[j] >= start_target and series.navs[j] > 0:
            if (target_date - series.dates[j]).days >= window_days - 5:  # tolerate small gaps
                r = (series.navs[i] / series.navs[j]) - 1.0
                results.append(r)
    return results


def rolling_return_distribution(returns: list[float]) -> dict:
    if not returns:
        return {}
    s = sorted(returns)
    n = len(s)

    def pct(p):
        idx = min(n - 1, max(0, int(round(p * (n - 1)))))
        return s[idx]

    return {
        "min": s[0], "p5": pct(0.05), "p25": pct(0.25), "median": pct(0.5),
        "p75": pct(0.75), "p95": pct(0.95), "max": s[-1],
        "pct_negative": sum(1 for r in s if r < 0) / n,
        "n_windows": n,
    }
