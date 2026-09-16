"""Phase 3: Benchmark proxy resolution via passive-fund NAV (spec §3.4).

True benchmark TRI series are not freely available in India. The spec recommends two
alternatives, both implemented here:

  Option 2 — Proxy via a low-TER passive fund's Direct-Growth NAV (free from AMFI,
              already in our pipeline). Biased downward by ~20-40 bps/yr TER + tracking
              error. Always labelled as a proxy; `comparator_type: "proxy"` is mandatory
              in every payload that uses this.

  Option 3 — Category median CAGR computed from the full ingested universe (already
              implemented in get_fund_performance for category comparisons). Also used as
              a fallback when no proxy fund is registered for an index.

A BENCHMARK_PROXIES registry maps common benchmark names (as they appear in portfolio
footer / factsheet strings) to an AMFI scheme code for an appropriate low-TER index fund.
This registry is hand-curated but cheap to extend — it is the only part of this module
that requires human judgement.

Design rules:
  - Never present a proxy NAV series as the benchmark index itself.
  - Always include proxy_fund_name, proxy_plan_id, and proxy_ter_caveat in provenance.
  - If the proxy fund's NAV series is shorter than the fund being measured, say so.
  - Never compute beta/alpha/IR against a proxy without clearly labelling the output
    as `k: "approximation"` in the provenance envelope.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from indian_mf_mcp.analytics.returns import NavSeries, from_rows, cagr, daily_returns
from indian_mf_mcp.store import repository as repo


# ---------------------------------------------------------------------------
# Benchmark proxy registry
# ---------------------------------------------------------------------------
# Maps partial benchmark name strings (lowercase, matched by substring) to the AMFI
# scheme code of a suitable Direct-Growth index fund proxy.
# Only add entries you have verified live — wrong proxies silently corrupt comparisons.

def _proxy_caveat(index_label: str) -> str:
    """Standard proxy-bias caveat: direction and rough magnitude, not just 'is_proxy'.

    The proxy fund's NAV already nets out its own TER + tracking error against the true
    index, so any fund-vs-"benchmark" gap computed from it is biased in the ACTIVE fund's
    favour (the proxy understates what the real index actually returned) by roughly the
    proxy's own cost drag — commonly 20-100 bps/yr depending on the index and fund.
    """
    return (
        f"Proxy: NAV series of a low-TER {index_label} index fund, not the licensed index "
        "itself. This biases the fund-vs-benchmark gap in the ACTIVE FUND'S FAVOUR by "
        "roughly the proxy's own cost drag (its TER + tracking error against the true "
        "index) — typically ~20-100 bps/yr. Use TRI values instead of this proxy wherever "
        "a licensed TRI series becomes available."
    )


BENCHMARK_PROXIES: dict[str, dict] = {
    # Nifty 50 / Sensex proxies
    "nifty 50 tri": {
        "scheme_code": "120503",   # UTI Nifty 50 Index Fund - Direct Growth (verified 2026-09)
        "label": "UTI Nifty 50 Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Nifty 50"),
    },
    "nifty 500 tri": {
        "scheme_code": "145552",   # Motilal Oswal Nifty 500 Index Fund - Direct Growth
        "label": "Motilal Oswal Nifty 500 Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Nifty 500"),
    },
    "nifty midcap 150 tri": {
        "scheme_code": "147622",   # Motilal Oswal Nifty Midcap 150 Index Fund - Direct Growth
        "label": "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Nifty Midcap 150"),
    },
    "nifty smallcap 250 tri": {
        "scheme_code": "147624",   # Motilal Oswal Nifty Smallcap 250 Index Fund - Direct Growth
        "label": "Motilal Oswal Nifty Smallcap 250 Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Nifty Smallcap 250"),
    },
    "bse sensex tri": {
        "scheme_code": "119598",   # HDFC Index Fund Sensex Plan - Direct Growth
        "label": "HDFC Index Fund Sensex Plan - Direct Growth",
        "caveat": _proxy_caveat("Sensex"),
    },
    "nifty next 50 tri": {
        "scheme_code": "120505",   # UTI Nifty Next 50 Index Fund - Direct Growth
        "label": "UTI Nifty Next 50 Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Nifty Next 50"),
    },
}


def _match_proxy(benchmark_name: str | None) -> dict | None:
    """Return the best matching proxy entry for a benchmark name, or None."""
    if not benchmark_name:
        return None
    bl = benchmark_name.lower()
    for key, entry in BENCHMARK_PROXIES.items():
        if key in bl:
            return entry
    return None


# ---------------------------------------------------------------------------
# Proxy resolution: scheme code → plan_id → NavSeries
# ---------------------------------------------------------------------------

def _plan_id_for_scheme_code(conn: sqlite3.Connection, scheme_code: str) -> str | None:
    row = conn.execute(
        "SELECT plan_id FROM plan WHERE amfi_scheme_code = ?", (scheme_code,)
    ).fetchone()
    return row["plan_id"] if row else None


def resolve_benchmark_proxy(
    conn: sqlite3.Connection,
    benchmark_name: str | None,
) -> dict | None:
    """Resolve benchmark_name to a proxy NavSeries + metadata.

    Returns a dict with keys:
      label, plan_id, series (NavSeries), caveat, comparator_type="proxy"
    or None if no proxy is registered for this benchmark.
    """
    entry = _match_proxy(benchmark_name)
    if entry is None:
        return None

    plan_id = _plan_id_for_scheme_code(conn, entry["scheme_code"])
    if plan_id is None:
        # Proxy fund not in local store (hasn't been ingested yet)
        return {
            "label": entry["label"],
            "plan_id": None,
            "series": None,
            "caveat": entry["caveat"] + " WARNING: proxy fund not yet in local NAV store; "
                      "ingest it with `mf-mcp ingest-navall` and `mf-mcp backfill-nav-history`.",
            "comparator_type": "proxy",
        }

    rows = repo.get_nav_series(conn, plan_id)
    series = from_rows(rows) if rows else None
    return {
        "label": entry["label"],
        "plan_id": plan_id,
        "series": series,
        "caveat": entry["caveat"],
        "comparator_type": "proxy",
    }


# ---------------------------------------------------------------------------
# Benchmark-relative metrics (labelled approximation per spec)
# ---------------------------------------------------------------------------

def benchmark_cagr(proxy: dict, start: date, end: date) -> float | None:
    """CAGR of the proxy series over [start, end]."""
    if proxy.get("series") is None:
        return None
    return cagr(proxy["series"], start, end)


def benchmark_daily_returns(proxy: dict) -> list[float]:
    if proxy.get("series") is None:
        return []
    return daily_returns(proxy["series"])
