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

import re
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
    # Every entry is pinned to BOTH the AMFI scheme code and the ISIN, plus the words the
    # plan's AMFI scheme name must contain. resolve_benchmark_proxy() re-checks all three
    # against the local store at call time, so a wrong code (as five of these once were —
    # 145552 is the Nasdaq 100 FoF, not a Nifty 500 fund) is refused rather than silently
    # used. Codes verified against AMFI NAVAll.txt on 2026-09-24.
    "nifty 50 tri": {
        "scheme_code": "120716",
        "isin": "INF789F01XA0",
        "name_must_contain": ["uti", "nifty 50 index"],
        "label": "UTI Nifty 50 Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Nifty 50"),
    },
    "nifty 500 tri": {
        "scheme_code": "147625",
        "isin": "INF247L01957",
        "name_must_contain": ["motilal oswal", "nifty 500 index"],
        "label": "Motilal Oswal Nifty 500 Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Nifty 500"),
    },
    "nifty midcap 150 tri": {
        "scheme_code": "147622",
        "isin": "INF247L01916",
        "name_must_contain": ["motilal oswal", "nifty midcap 150 index"],
        "label": "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Nifty Midcap 150"),
    },
    "nifty smallcap 250 tri": {
        "scheme_code": "147623",
        "isin": "INF247L01932",
        "name_must_contain": ["motilal oswal", "nifty smallcap 250 index"],
        "label": "Motilal Oswal Nifty Smallcap 250 Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Nifty Smallcap 250"),
    },
    "bse sensex tri": {
        "scheme_code": "119065",
        "isin": "INF179K01WN9",
        "name_must_contain": ["hdfc", "sensex index"],
        "label": "HDFC BSE Sensex Index Fund - Direct Growth",
        "caveat": _proxy_caveat("Sensex"),
    },
    "nifty next 50 tri": {
        "scheme_code": "143341",
        "isin": "INF789FC12T1",
        "name_must_contain": ["uti", "nifty next 50 index"],
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

def _proxy_plan_row(conn: sqlite3.Connection, scheme_code: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT p.plan_id, p.isin, p.plan_type, p.option_type, s.name AS scheme_name
           FROM plan p JOIN scheme s ON s.scheme_id = p.scheme_id
           WHERE p.amfi_scheme_code = ?""",
        (scheme_code,),
    ).fetchone()


def _normalise_name(name: str) -> str:
    # AMFI spells the same fund "UTI - Nifty Next 50 Index Fund", "HDFC BSE Sensex Index
    # Fund", etc. — compare on lowercase words only so punctuation can't cause a false alarm.
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def _registry_mismatch(entry: dict, row: sqlite3.Row) -> str | None:
    """Why the stored plan behind entry['scheme_code'] is NOT the fund the registry claims,
    or None if it checks out."""
    problems = []
    if entry.get("isin") and row["isin"] and row["isin"] != entry["isin"]:
        problems.append(f"ISIN is {row['isin']}, registry expects {entry['isin']}")
    name = _normalise_name(row["scheme_name"] or "")
    missing = [w for w in entry.get("name_must_contain", []) if _normalise_name(w) not in name]
    if missing:
        problems.append(f"scheme name {row['scheme_name']!r} lacks {missing}")
    if (row["plan_type"], row["option_type"]) != ("Direct", "Growth"):
        problems.append(f"plan is {row['plan_type']}/{row['option_type']}, not Direct/Growth")
    return "; ".join(problems) or None


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

    row = _proxy_plan_row(conn, entry["scheme_code"])
    if row is None:
        # Proxy fund not in local store (hasn't been ingested yet)
        return {
            "label": entry["label"],
            "plan_id": None,
            "series": None,
            "caveat": entry["caveat"] + " WARNING: proxy fund not yet in local NAV store; "
                      "ingest it with `mf-mcp ingest-navall` and `mf-mcp backfill-nav-history`.",
            "comparator_type": "proxy",
        }

    mismatch = _registry_mismatch(entry, row)
    if mismatch:
        # A wrong proxy doesn't fail loudly — it produces plausible-looking but meaningless
        # beta/alpha/capture numbers. Refuse it instead.
        return {
            "label": entry["label"],
            "plan_id": row["plan_id"],
            "series": None,
            "caveat": entry["caveat"],
            "comparator_type": "proxy",
            "registry_mismatch": (
                f"Benchmark proxy registry entry for AMFI code {entry['scheme_code']} does not "
                f"match the local store ({mismatch}); benchmark comparison withheld. Fix "
                "BENCHMARK_PROXIES in analytics/benchmark.py."
            ),
        }

    plan_id = row["plan_id"]
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


def aligned_daily_returns(fund: NavSeries, bench: NavSeries) -> tuple[list[float], list[float]]:
    """Fund and benchmark daily returns paired BY DATE over their common NAV dates.

    beta/IR/alpha/capture pair the i-th fund return with the i-th benchmark return. Feeding
    them two independently-computed return lists tail-aligns by position, so a single date
    present in one series but not the other (a per-AMC NAV filing gap, a fund that launched
    later, an ingest miss) shifts every earlier pair by a day. Intersect dates first.
    """
    bench_nav = dict(zip(bench.dates, bench.navs))
    common = [(d, v, bench_nav[d]) for d, v in zip(fund.dates, fund.navs) if d in bench_nav]
    f_rets, b_rets = [], []
    for (_, f0, b0), (_, f1, b1) in zip(common, common[1:]):
        if f0 > 0 and b0 > 0:
            f_rets.append(f1 / f0 - 1)
            b_rets.append(b1 / b0 - 1)
    return f_rets, b_rets
