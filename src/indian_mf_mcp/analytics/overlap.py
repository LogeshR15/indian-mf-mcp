"""Portfolio overlap — ISIN set intersection weighted by %NAV (spec §12).

"portfolio overlap between funds (set intersection weighted by %NAV — cheap, mechanical,
and needed for the comparison use case)."

When scheme_ids has two or more entries in get_fund_portfolio, the tool adds an 'overlap'
section comparing every pair. This module does the pure computation; the tool handles
provenance wrapping.

Definitions:
    overlap_pct_a  = sum of %NAV in fund A for ISINs that also appear in fund B
    overlap_pct_b  = same from B's perspective
    These are asymmetric: a large fund holding 5% of its NAV in a stock that makes up
    40% of a small fund means overlap_pct_a = 5 but overlap_pct_b = 40.

All inputs are lists of SQLite Row or dict with at minimum:
    {isin, instrument_name, pct_nav, asset_class}

Only equity, foreign, and reit rows are considered (same population as concentration stats).
Cash, debt, derivatives are excluded — their 'overlap' is not informative for portfolio
analysis.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OverlapResult:
    scheme_id_a: str
    scheme_id_b: str
    # Pct of A's NAV invested in ISINs also held by B
    overlap_pct_a: float
    # Pct of B's NAV invested in ISINs also held by A
    overlap_pct_b: float
    # Simple average of the two — a symmetric summary statistic
    overlap_pct_avg: float
    common_count: int        # number of shared ISINs (equity/foreign/reit only)
    total_count_a: int
    total_count_b: int
    # Top common holdings sorted by average weight desc
    common_holdings: list[dict]  # [{isin, name, pct_nav_a, pct_nav_b, avg_pct_nav}]


_EQUITY_LIKE = {"equity", "foreign", "reit"}


def _equity_isin_map(holdings: list) -> dict[str, dict]:
    """Build ISIN → {name, pct_nav} map for equity-like rows only. Skip null ISINs."""
    result: dict[str, dict] = {}
    for h in holdings:
        if (h["asset_class"] or "other") not in _EQUITY_LIKE:
            continue
        isin = h["isin"]
        if not isin:
            continue
        pct = h["pct_nav"] or 0.0
        name = h["instrument_name"] or ""
        # If same ISIN appears twice (e.g. listed + unlisted sub-buckets), sum %NAV
        if isin in result:
            result[isin]["pct_nav"] += pct
        else:
            result[isin] = {"name": name, "pct_nav": pct}
    return result


def portfolio_overlap(
    holdings_a: list,
    holdings_b: list,
    scheme_id_a: str,
    scheme_id_b: str,
) -> OverlapResult:
    """Compute pairwise portfolio overlap.

    Args:
        holdings_a: rows from prepo.get_holdings for scheme A
        holdings_b: rows from prepo.get_holdings for scheme B
        scheme_id_a: string scheme identifier for A
        scheme_id_b: string scheme identifier for B

    Returns:
        OverlapResult with full detail
    """
    map_a = _equity_isin_map(holdings_a)
    map_b = _equity_isin_map(holdings_b)

    common_isins = set(map_a) & set(map_b)

    overlap_pct_a = sum(map_a[i]["pct_nav"] for i in common_isins)
    overlap_pct_b = sum(map_b[i]["pct_nav"] for i in common_isins)
    avg = (overlap_pct_a + overlap_pct_b) / 2.0 if common_isins else 0.0

    common_holdings = sorted(
        [
            {
                "isin": isin,
                "name": map_a[isin]["name"] or map_b[isin]["name"],
                "pct_nav_a": round(map_a[isin]["pct_nav"], 2),
                "pct_nav_b": round(map_b[isin]["pct_nav"], 2),
                "avg_pct_nav": round((map_a[isin]["pct_nav"] + map_b[isin]["pct_nav"]) / 2, 2),
            }
            for isin in common_isins
        ],
        key=lambda x: x["avg_pct_nav"],
        reverse=True,
    )

    return OverlapResult(
        scheme_id_a=scheme_id_a,
        scheme_id_b=scheme_id_b,
        overlap_pct_a=round(overlap_pct_a, 2),
        overlap_pct_b=round(overlap_pct_b, 2),
        overlap_pct_avg=round(avg, 2),
        common_count=len(common_isins),
        total_count_a=len(map_a),
        total_count_b=len(map_b),
        common_holdings=common_holdings,
    )


def all_pairs_overlap(
    scheme_holdings: dict[str, list],
) -> list[OverlapResult]:
    """Compute pairwise overlap for all pairs in scheme_holdings.

    Args:
        scheme_holdings: {scheme_id: holdings_rows}

    Returns:
        List of OverlapResult, one per unique pair (order: (A,B) where A < B lexically)
    """
    ids = sorted(scheme_holdings)
    results = []
    for i, id_a in enumerate(ids):
        for id_b in ids[i + 1:]:
            results.append(
                portfolio_overlap(
                    scheme_holdings[id_a],
                    scheme_holdings[id_b],
                    id_a,
                    id_b,
                )
            )
    return results
