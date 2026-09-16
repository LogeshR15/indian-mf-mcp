"""get_fund_performance: the complete return/risk evidence pack, one methodology for every fund.

Never computed by Claude directly (spec §5.2) — 2500+ daily points per fund, rolling-window
stats over 10 years, and cross-fund comparability all require one fixed methodology here.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from indian_mf_mcp import config
from indian_mf_mcp.analytics import drawdown as dd
from indian_mf_mcp.analytics import returns as R
from indian_mf_mcp.analytics import risk as risk_mod
from indian_mf_mcp.analytics.benchmark import (
    resolve_benchmark_proxy, benchmark_cagr, benchmark_daily_returns,
)
from indian_mf_mcp.normalize.taxonomy import is_segregated_portfolio_name
from indian_mf_mcp.provenance.wrapper import ProvenanceBuilder
from indian_mf_mcp.store import repository as repo

STANDARD_HORIZONS = [1, 3, 5, 7, 10]


_KNOWN_PLANS = {
    "direct_growth": ("Direct", "Growth"),
    "regular_growth": ("Regular", "Growth"),
    "direct_idcw": ("Direct", "IDCW"),
    "regular_idcw": ("Regular", "IDCW"),
}


def _pick_plan(conn: sqlite3.Connection, scheme_id: str, plan: str) -> tuple[sqlite3.Row | None, str | None]:
    """Resolve the requested plan (e.g. "regular_growth") to a concrete plan row.

    Returns (row, warning). The requested `plan` was previously accepted by the tool
    signature but silently ignored — every call returned Direct/Growth regardless of
    what was asked for. Falls back to Direct/Growth (then any Direct plan, then any
    plan at all) only when the exact requested plan/option combination doesn't exist,
    and always says so via the warning rather than substituting silently.
    """
    plans = repo.get_plans_for_scheme(conn, scheme_id)
    if not plans:
        return None, None

    target = _KNOWN_PLANS.get(plan)
    if target is None:
        warning = (
            f"Unrecognised plan={plan!r}; expected one of {sorted(_KNOWN_PLANS)}. "
            "Falling back to direct_growth."
        )
        target = _KNOWN_PLANS["direct_growth"]
    else:
        warning = None

    plan_type, option_type = target
    for p in plans:
        if p["plan_type"] == plan_type and p["option_type"] == option_type:
            return p, warning

    fallback_warning = (
        f"Requested plan={plan!r} but no {plan_type}/{option_type} plan exists for "
        f"{scheme_id}; "
    )
    for p in plans:
        if p["plan_type"] == "Direct" and p["option_type"] == "Growth":
            return p, fallback_warning + "used Direct/Growth instead."
    for p in plans:
        if p["plan_type"] == "Direct":
            return p, fallback_warning + f"used Direct/{p['option_type']} instead."
    return plans[0], fallback_warning + f"used {plans[0]['plan_type']}/{plans[0]['option_type']} instead."


def _series_for_plan(conn: sqlite3.Connection, plan_id: str) -> R.NavSeries:
    rows = repo.get_nav_series(conn, plan_id)
    return R.from_rows(rows)


def _category_stats(
    conn: sqlite3.Connection,
    scheme_id: str,
    category: str,
    years: float,
    as_of: date,
) -> dict:
    """Category comparator computed from the full ingested universe (spec §3.4 option 3).

    Returns a dict with median, p25, p75, n_funds, rank, percentile.
    Always includes a survivorship-bias caveat (spec §15):
      "Category statistics include only currently-active schemes. Wound-up, merged, or
       discontinued schemes are excluded — this introduces upward survivorship bias of
       unknown magnitude."

    Segregated (side-pocketed) portfolios are also excluded (spec §9.2: "detect and surface;
    don't merge") — a side-pocket's return series reflects a defaulted-exposure workout, not
    a fund manager's ordinary performance, so averaging it into a category median would
    silently distort the comparator for every other fund in that category.
    """
    rows = conn.execute(
        """SELECT p.plan_id, p.scheme_id, s.name FROM plan p
           JOIN scheme s ON s.scheme_id = p.scheme_id
           WHERE s.category = ? AND p.plan_type = 'Direct' AND p.option_type = 'Growth'
             AND p.active = 1""",
        (category,),
    ).fetchall()
    rows = [r for r in rows if not is_segregated_portfolio_name(r["name"])]
    start = as_of - timedelta(days=int(years * 365.25))
    vals: list[float] = []
    this_val: float | None = None
    for r in rows:
        series = _series_for_plan(conn, r["plan_id"])
        c = R.cagr(series, start, as_of)
        if c is not None:
            vals.append(c)
            if r["scheme_id"] == scheme_id:
                this_val = c

    n = len(vals)
    if n == 0:
        return {
            "median": None, "p25": None, "p75": None,
            "n_funds": 0, "rank": None, "percentile": None,
            "caveat": (
                "No comparable funds with sufficient NAV history found in the local store. "
                "Run 'mf-mcp backfill-nav-history' to populate the universe."
            ),
        }

    vals.sort()
    median = vals[n // 2]
    p25 = vals[max(0, n // 4)]
    p75 = vals[min(n - 1, (3 * n) // 4)]

    # Rank and percentile (1 = best)
    rank: int | None = None
    percentile: float | None = None
    if this_val is not None:
        rank = sum(1 for v in vals if v > this_val) + 1  # rank 1 = highest CAGR
        percentile = round(100.0 * (1 - (rank - 1) / n), 1)

    return {
        "median": round(median, 2),
        "p25": round(p25, 2),
        "p75": round(p75, 2),
        "n_funds": n,
        "rank": rank,
        "percentile": percentile,
        "survivorship_bias_caveat": (
            "Category statistics are computed from currently-active schemes only. "
            "Schemes that were wound up, merged into other funds, or discontinued due to "
            "underperformance are excluded — this biases all figures upward by an unknown "
            "magnitude. This is an inherent limitation of public MF disclosure data. "
            "Segregated (side-pocketed) portfolios are also excluded by name-pattern match — "
            "their return series reflects a defaulted-exposure workout, not ordinary "
            "fund-manager performance."
        ),
    }


def get_fund_performance(
    conn: sqlite3.Connection,
    scheme_ids: list[str],
    plan: str = "direct_growth",
    period: str = "10Y",
    comparators: list[str] | None = None,
    metrics: list[str] | None = None,
    rolling_windows: list[int] | None = None,
    nav_series: bool = False,
    provenance: str = "compact",
    risk_free_annual: float | None = None,
) -> dict:
    # Sharpe/Sortino/alpha stay comparable across funds at the same point in time (all
    # computed with the same rate), but a stated CONSTANT is not comparable across time
    # windows spanning a repo-rate move (e.g. 4%->6.5%). Making the rate an explicit,
    # always-echoed parameter lets a caller pin it for genuine cross-period comparisons
    # instead of silently inheriting whatever the current default happens to be.
    risk_free_annual = (
        config.DEFAULT_RISK_FREE_RATE_ANNUAL if risk_free_annual is None else risk_free_annual
    )
    comparators = comparators or ["category"]
    metrics = metrics or ["trailing", "rolling", "risk", "drawdown", "stress"]
    rolling_windows = rolling_windows or [1, 3, 5]
    as_of = date.today()

    results = {}
    for scheme_id in scheme_ids:
        pb = ProvenanceBuilder()
        chosen_plan, plan_warning = _pick_plan(conn, scheme_id, plan)
        if not chosen_plan:
            results[scheme_id] = {"error": "no_plans_found", "meta": {"scheme_id": scheme_id}}
            continue

        if chosen_plan["option_type"] == "IDCW":
            # AMFI's NAVAll/NAV-history feed publishes raw NAV, not a distribution-adjusted
            # (total-return) series. Computing CAGR/return metrics directly off an IDCW
            # plan's NAV silently understates every return by whatever was paid out as
            # income distributions — the number still looks plausible, so nothing else
            # would catch it. Refuse rather than emit a confidently wrong figure.
            results[scheme_id] = {
                "error": "idcw_plan_return_analytics_unsupported",
                "meta": {
                    "scheme_id": scheme_id,
                    "plan_id": chosen_plan["plan_id"],
                    "plan_requested": plan,
                    "note": (
                        "This plan is an IDCW (dividend/payout) option. AMFI NAV history for "
                        "IDCW plans is not distribution-adjusted, so CAGR/volatility/Sharpe "
                        "computed from it would systematically understate real returns with no "
                        "warning that would catch it. Request a Growth plan instead "
                        "(plan='direct_growth' or 'regular_growth'); if this scheme has no "
                        "Growth plan, return analytics are genuinely unavailable for it."
                    ),
                },
            }
            continue

        if plan_warning:
            pb.warn(plan_warning)

        series = _series_for_plan(conn, chosen_plan["plan_id"])
        if len(series) < 2:
            results[scheme_id] = {"error": "insufficient_nav_history", "meta": {"scheme_id": scheme_id}}
            continue

        src_nav = pb.add_source(
            type="amfi_nav_history", plan_id=chosen_plan["plan_id"],
            coverage_start=series.dates[0].isoformat(), coverage_end=series.dates[-1].isoformat(),
        )

        # Anchor calculations to the latest actually-available NAV date, never to "today" —
        # otherwise a stale/cold-cache fund would silently report None instead of its real
        # most-recent numbers. Staleness itself is surfaced as an explicit warning.
        effective_as_of = min(as_of, series.dates[-1])
        if effective_as_of < as_of:
            pb.warn(f"Latest available NAV is {effective_as_of.isoformat()}, "
                    f"older than today ({as_of.isoformat()}); metrics anchored to that date.")

        if "trailing" in metrics:
            for h in STANDARD_HORIZONS:
                start = effective_as_of - timedelta(days=int(h * 365.25))
                val = R.cagr(series, start, effective_as_of)
                calc = pb.add_calc(method="cagr_daily_nav", inputs=[src_nav],
                                    params={"plan": plan, "horizon_years": h, "as_of": effective_as_of.isoformat()})
                pb.fact(f"cagr_{h}y", val, calc, "calculated")
            si_val = R.since_inception_cagr(series)
            calc = pb.add_calc(method="cagr_since_inception", inputs=[src_nav])
            pb.fact("cagr_since_inception", si_val, calc, "calculated")

        daily_rets = R.daily_returns(series)

        if "risk" in metrics:
            calc = pb.add_calc(method="annualised_vol_daily", inputs=[src_nav])
            pb.fact("volatility_annualised", risk_mod.annualised_volatility(daily_rets), calc, "calculated")
            calc = pb.add_calc(method="sharpe_ratio", inputs=[src_nav],
                                params={"risk_free_annual": risk_free_annual})
            pb.fact("sharpe_ratio", risk_mod.sharpe_ratio(daily_rets, risk_free_annual), calc, "calculated",
                    caveat=(
                        f"Risk-free rate is a stated constant ({risk_free_annual:.4f} annualised, "
                        "not a live RBI T-bill feed) — comparable across funds computed at the same "
                        "time, NOT comparable across time windows spanning a rate move. Pass "
                        "risk_free_annual explicitly to pin the rate for cross-period comparisons."
                    ))
            calc = pb.add_calc(method="sortino_ratio", inputs=[src_nav],
                                params={"risk_free_annual": risk_free_annual})
            pb.fact("sortino_ratio", risk_mod.sortino_ratio(daily_rets, risk_free_annual), calc, "calculated")

        if "rolling" in metrics:
            rolling_out = {}
            for w in rolling_windows:
                rr = R.rolling_returns(series, w)
                rolling_out[f"{w}Y"] = R.rolling_return_distribution(rr)
            calc = pb.add_calc(method="rolling_returns", inputs=[src_nav], params={"windows_years": rolling_windows})
            pb.fact("rolling_returns", rolling_out, calc, "calculated")

        if "drawdown" in metrics:
            drawdowns = dd.find_drawdowns(series, min_depth_pct=0.10)
            calc = pb.add_calc(method="drawdown_detection", inputs=[src_nav], params={"min_depth_pct": 0.10})
            pb.fact("drawdowns_gt_10pct", [
                {"peak_date": d.peak_date.isoformat(), "trough_date": d.trough_date.isoformat(),
                 "depth_pct": d.depth_pct, "duration_days": d.duration_days,
                 "recovery_date": d.recovery_date.isoformat() if d.recovery_date else None,
                 "recovery_days": d.recovery_days}
                for d in drawdowns
            ], calc, "calculated")

        if "stress" in metrics:
            stress_out = {}
            for name, (s, e) in dd.STRESS_WINDOWS.items():
                stress_out[name] = {
                    "start": s.isoformat(), "end": e.isoformat(),
                    "return": dd.stress_window_return(series, s, e),
                }
            calc = pb.add_calc(method="stress_window_return", inputs=[src_nav])
            pb.fact("stress_windows", stress_out, calc, "calculated")

        if "category" in comparators:
            taxonomy = repo.latest_taxonomy(conn, scheme_id)
            category = taxonomy["category"] if taxonomy else None
            if category:
                cat_stats = _category_stats(conn, scheme_id, category, 5.0, effective_as_of)
                calc = pb.add_calc(
                    method="category_stats_5y",
                    params={"category": category, "years": 5},
                    caveat=cat_stats.get("survivorship_bias_caveat", ""),
                )
                pb.fact(
                    "category_stats_5y",
                    cat_stats,
                    calc,
                    "calculated",
                    caveat=(
                        "Category comparator computed from live Direct/Growth schemes only. "
                        "Survivorship bias is present — see survivorship_bias_caveat field. "
                        "Not benchmark TRI (see benchmark proxy limitation)."
                    ),
                )

        if "benchmark" in comparators:
            # Resolve benchmark name from portfolio snapshot footer (most current source)
            snap = conn.execute(
                "SELECT benchmark_name FROM portfolio_snapshot WHERE scheme_id = ? "
                "ORDER BY as_of_date DESC LIMIT 1",
                (scheme_id,),
            ).fetchone()
            bench_name = snap["benchmark_name"] if snap else None
            proxy = resolve_benchmark_proxy(conn, bench_name)
            if proxy is None:
                pb.warn(
                    f"No benchmark proxy registered for {bench_name!r}. "
                    "Benchmark comparison unavailable. Category comparator is used instead "
                    "(add to analytics/benchmark.py BENCHMARK_PROXIES if a suitable index fund "
                    "NAV is available in the local store)."
                )
            elif proxy.get("series") is None:
                pb.warn(
                    f"Benchmark proxy '{proxy['label']}' is registered but its NAV is not yet "
                    "in the local store. Run `mf-mcp ingest-navall` and "
                    "`mf-mcp backfill-nav-history` to populate it."
                )
            else:
                src_bench = pb.add_source(
                    type="benchmark_proxy_nav",
                    proxy_label=proxy["label"],
                    proxy_plan_id=proxy["plan_id"],
                    comparator_type="proxy",
                    caveat=proxy["caveat"],
                )
                bench_rets = benchmark_daily_returns(proxy)

                # Trailing CAGR vs benchmark proxy
                bench_trailing = {}
                for h in STANDARD_HORIZONS:
                    start = effective_as_of - timedelta(days=int(h * 365.25))
                    bval = benchmark_cagr(proxy, start, effective_as_of)
                    bench_trailing[f"{h}Y"] = bval
                calc = pb.add_calc(method="cagr_benchmark_proxy", inputs=[src_bench],
                                    params={"horizons_years": STANDARD_HORIZONS})
                pb.fact("benchmark_cagr_trailing", bench_trailing, calc, "approximation",
                        caveat=proxy["caveat"])

                # Risk-adjusted vs benchmark (beta, IR, alpha, up/down capture)
                if bench_rets and "risk" in metrics:
                    calc_b = pb.add_calc(method="benchmark_relative_risk",
                                          inputs=[src_nav, src_bench])
                    pb.fact("beta", risk_mod.beta(daily_rets, bench_rets), calc_b, "approximation",
                            caveat=proxy["caveat"])
                    pb.fact("information_ratio", risk_mod.information_ratio(daily_rets, bench_rets),
                            calc_b, "approximation", caveat=proxy["caveat"])
                    pb.fact("alpha", risk_mod.alpha(daily_rets, bench_rets, risk_free_annual), calc_b, "approximation",
                            caveat=proxy["caveat"])
                    udc = risk_mod.up_down_capture(daily_rets, bench_rets)
                    pb.fact("up_capture", udc["up_capture"], calc_b, "approximation",
                            caveat=proxy["caveat"])
                    pb.fact("down_capture", udc["down_capture"], calc_b, "approximation",
                            caveat=proxy["caveat"])

        if nav_series:
            pb.fact("nav_series", [{"date": dt.isoformat(), "nav": v} for dt, v in zip(series.dates, series.navs)],
                    src_nav, "official")

        results[scheme_id] = pb.build(provenance=provenance, extra_meta={
            "scheme_id": scheme_id, "plan_id": chosen_plan["plan_id"],
            "plan_requested": plan, "as_of": as_of.isoformat(),
            "coverage_start": series.dates[0].isoformat(), "coverage_end": series.dates[-1].isoformat(),
        })

    return results
