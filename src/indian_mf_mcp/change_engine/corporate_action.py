"""Corporate-action heuristics: flag, never assert. Splits/bonuses/mergers produce quantity
jumps with no real trade — asserting a trade there is a false signal (spec §9.2)."""
from __future__ import annotations

from fractions import Fraction

# Tolerance for treating a qty ratio as "looks like a clean corporate-action multiple"
# (typical splits/bonuses: 1:1, 1:2, 3:2, 5:4 ... small numerator+denominator). Any ratio can
# be approximated by *some* small fraction (Dirichlet), so denominator and num+denom must both
# stay small, or every ordinary trade would get flagged.
_RATIO_DENOM_LIMIT = 8
_RATIO_SUM_LIMIT = 12
_RATIO_TOLERANCE = 0.005
_PCT_NAV_STABLE_TOLERANCE = 0.15  # relative change in %NAV considered "roughly unchanged"


def suspected_split_or_bonus(prev_qty: float, curr_qty: float, prev_pct_nav: float | None,
                              curr_pct_nav: float | None) -> bool:
    """Δqty ratio close to a simple rational AND %NAV roughly unchanged => flag, don't assert."""
    if prev_qty in (None, 0) or curr_qty in (None, 0):
        return False
    ratio = curr_qty / prev_qty
    if ratio <= 0 or abs(ratio - 1.0) < 1e-6:
        return False
    frac = Fraction(ratio).limit_denominator(_RATIO_DENOM_LIMIT)
    is_clean_ratio = (
        abs(float(frac) - ratio) < _RATIO_TOLERANCE
        and (frac.numerator + frac.denominator) <= _RATIO_SUM_LIMIT
    )
    if not is_clean_ratio:
        return False
    if prev_pct_nav in (None, 0) or curr_pct_nav is None:
        return True  # can't check %NAV stability; still flag on the ratio alone, low-confidence
    pct_nav_ratio_change = abs((curr_pct_nav / prev_pct_nav) - 1.0)
    return pct_nav_ratio_change < _PCT_NAV_STABLE_TOLERANCE


def suspected_merger(exited_value: float | None, new_value: float | None,
                      tolerance: float = 0.2) -> bool:
    """Same-month ISIN exit + new ISIN entry of similar market value => possible merger/
    restructuring, not a genuine exit+new-buy. Flag only."""
    if exited_value is None or new_value is None or exited_value == 0:
        return False
    return abs((new_value / exited_value) - 1.0) < tolerance
