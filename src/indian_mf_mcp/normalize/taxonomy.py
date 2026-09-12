"""Derive (plan_type, option_type, idcw_variant, base_scheme_name) from an AMFI scheme-name
string, and roll up SEBI category/sub_category strings.

AMFI's NAVAll.txt does not carry Direct/Regular or Growth/IDCW as separate columns — they are
embedded in the free-text scheme name (e.g. "Axis Bluechip Fund - Direct Plan - Growth").
This parsing is therefore marked `inferred`, not `official`, wherever it feeds a provenance
payload — it is a reliable heuristic (AMFI naming is fairly consistent) but not a guaranteed field.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_DIRECT_RE = re.compile(r"\bDirect\b", re.IGNORECASE)
_REGULAR_RE = re.compile(r"\bRegular\b", re.IGNORECASE)
_GROWTH_RE = re.compile(r"\bGrowth\b", re.IGNORECASE)
_IDCW_RE = re.compile(r"\b(IDCW|Dividend|Income Distribution)\b", re.IGNORECASE)
_REINVEST_RE = re.compile(r"\b(Reinvest(ment)?)\b", re.IGNORECASE)
_PAYOUT_RE = re.compile(r"\bPayout\b", re.IGNORECASE)

# tokens stripped out (case-insensitive, with surrounding separators) to derive a base name
_STRIP_TOKENS = [
    r"-?\s*Direct Plan", r"-?\s*Regular Plan", r"-?\s*Direct", r"-?\s*Regular",
    r"-?\s*Growth Option", r"-?\s*Growth", r"-?\s*IDCW Reinvestment", r"-?\s*IDCW Payout",
    r"-?\s*IDCW", r"-?\s*Dividend Reinvestment", r"-?\s*Dividend Payout", r"-?\s*Dividend",
    r"-?\s*Plan", r"-?\s*Option",
]
_STRIP_RE = re.compile("|".join(_STRIP_TOKENS), re.IGNORECASE)
_WS_RE = re.compile(r"\s{2,}")
_TRAILING_SEP_RE = re.compile(r"[\s\-]+$")


@dataclass
class PlanNameInfo:
    plan_type: str | None       # "Direct" | "Regular" | None (undetermined)
    option_type: str | None     # "Growth" | "IDCW" | None
    idcw_variant: str | None    # "Payout" | "Reinvest" | None
    base_scheme_name: str       # name with plan/option tokens stripped, for grouping


def parse_plan_option(scheme_name: str) -> PlanNameInfo:
    plan_type = "Direct" if _DIRECT_RE.search(scheme_name) else (
        "Regular" if _REGULAR_RE.search(scheme_name) else None
    )
    is_idcw = bool(_IDCW_RE.search(scheme_name))
    option_type = "IDCW" if is_idcw else ("Growth" if _GROWTH_RE.search(scheme_name) else None)
    idcw_variant = None
    if is_idcw:
        if _REINVEST_RE.search(scheme_name):
            idcw_variant = "Reinvest"
        elif _PAYOUT_RE.search(scheme_name):
            idcw_variant = "Payout"

    base = _STRIP_RE.sub("", scheme_name)
    base = _WS_RE.sub(" ", base).strip()
    base = _TRAILING_SEP_RE.sub("", base).strip()
    return PlanNameInfo(plan_type, option_type, idcw_variant, base or scheme_name.strip())


# Hand-written roll-up map: SEBI sub-categories -> broad asset roll-up.
# Not exhaustive; unmatched sub-categories roll up to "Other" and should be treated as
# `inferred`, never silently miscategorized as one of the known buckets.
CATEGORY_ROLLUP = {
    "Equity Scheme": "Equity",
    "Debt Scheme": "Debt",
    "Hybrid Scheme": "Hybrid",
    "Solution Oriented Scheme": "Solution Oriented",
    "Other Scheme": "Other",
}


def rollup_category(category: str) -> str:
    return CATEGORY_ROLLUP.get(category.strip(), "Other")
