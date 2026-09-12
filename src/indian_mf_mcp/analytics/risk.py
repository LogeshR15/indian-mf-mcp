"""Volatility, Sharpe, Sortino, beta, drawdown-adjacent risk stats. Computed from daily returns."""
from __future__ import annotations

import math

from indian_mf_mcp import config


def annualised_volatility(daily_rets: list[float]) -> float | None:
    if len(daily_rets) < 2:
        return None
    mean = sum(daily_rets) / len(daily_rets)
    var = sum((r - mean) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def annualised_return_from_daily(daily_rets: list[float]) -> float | None:
    if not daily_rets:
        return None
    mean = sum(daily_rets) / len(daily_rets)
    return (1 + mean) ** 252 - 1


def sharpe_ratio(daily_rets: list[float], risk_free_annual: float = config.DEFAULT_RISK_FREE_RATE_ANNUAL) -> float | None:
    vol = annualised_volatility(daily_rets)
    ann_ret = annualised_return_from_daily(daily_rets)
    if vol is None or ann_ret is None or vol == 0:
        return None
    return (ann_ret - risk_free_annual) / vol


def sortino_ratio(daily_rets: list[float], risk_free_annual: float = config.DEFAULT_RISK_FREE_RATE_ANNUAL) -> float | None:
    if len(daily_rets) < 2:
        return None
    downside = [min(0.0, r) for r in daily_rets]
    downside_dev = math.sqrt(sum(d ** 2 for d in downside) / len(downside)) * math.sqrt(252)
    ann_ret = annualised_return_from_daily(daily_rets)
    if ann_ret is None or downside_dev == 0:
        return None
    return (ann_ret - risk_free_annual) / downside_dev


def beta(fund_rets: list[float], bench_rets: list[float]) -> float | None:
    n = min(len(fund_rets), len(bench_rets))
    if n < 2:
        return None
    f, b = fund_rets[-n:], bench_rets[-n:]
    mean_f = sum(f) / n
    mean_b = sum(b) / n
    cov = sum((f[i] - mean_f) * (b[i] - mean_b) for i in range(n)) / (n - 1)
    var_b = sum((b[i] - mean_b) ** 2 for i in range(n)) / (n - 1)
    if var_b == 0:
        return None
    return cov / var_b


def information_ratio(fund_rets: list[float], bench_rets: list[float]) -> float | None:
    n = min(len(fund_rets), len(bench_rets))
    if n < 2:
        return None
    active = [fund_rets[-n:][i] - bench_rets[-n:][i] for i in range(n)]
    mean_active = sum(active) / n
    var = sum((a - mean_active) ** 2 for a in active) / (n - 1)
    tracking_error = math.sqrt(var) * math.sqrt(252)
    if tracking_error == 0:
        return None
    ann_active = (1 + mean_active) ** 252 - 1
    return ann_active / tracking_error


def alpha(fund_rets: list[float], bench_rets: list[float], risk_free_annual: float = config.DEFAULT_RISK_FREE_RATE_ANNUAL) -> float | None:
    b = beta(fund_rets, bench_rets)
    ann_f = annualised_return_from_daily(fund_rets)
    ann_b = annualised_return_from_daily(bench_rets)
    if b is None or ann_f is None or ann_b is None:
        return None
    return ann_f - (risk_free_annual + b * (ann_b - risk_free_annual))


def up_down_capture(fund_rets: list[float], bench_rets: list[float]) -> dict:
    n = min(len(fund_rets), len(bench_rets))
    f, b = fund_rets[-n:], bench_rets[-n:]
    up_f = [f[i] for i in range(n) if b[i] > 0]
    up_b = [b[i] for i in range(n) if b[i] > 0]
    down_f = [f[i] for i in range(n) if b[i] < 0]
    down_b = [b[i] for i in range(n) if b[i] < 0]

    def ratio(fs, bs):
        if not bs:
            return None
        sf, sb = sum(fs), sum(bs)
        if sb == 0:
            return None
        return sf / sb

    return {"up_capture": ratio(up_f, up_b), "down_capture": ratio(down_f, down_b)}
