"""Concentration metrics: top-N, HHI, effective N. Deterministic, from a single snapshot."""
from __future__ import annotations


def concentration_stats(pct_navs: list[float]) -> dict:
    """pct_navs: fractional %NAV per equity/security holding (positive positions only,
    typically excluding cash/derivatives — caller decides the population)."""
    if not pct_navs:
        return {"top5_pct": None, "top10_pct": None, "top20_pct": None, "hhi": None,
                "effective_n": None, "n_holdings": 0}
    sorted_desc = sorted(pct_navs, reverse=True)

    def top_n_sum(n):
        return sum(sorted_desc[:n])

    hhi = sum(p ** 2 for p in pct_navs)  # p in fraction-of-1 terms
    effective_n = (1 / hhi) if hhi > 0 else None
    return {
        "top5_pct": top_n_sum(5),
        "top10_pct": top_n_sum(10),
        "top20_pct": top_n_sum(20),
        "hhi": hhi,
        "effective_n": effective_n,
        "n_holdings": len(pct_navs),
    }
