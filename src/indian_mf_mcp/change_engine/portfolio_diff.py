"""ISIN-keyed portfolio diff engine. Compare quantity, not %NAV, for trade signals (spec §9.1):
%NAV moves with price/flows alone, with no trade at all. Every row carries a confidence and
flags[] so a corporate action or price/flow drift is never silently asserted as a trade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from indian_mf_mcp.change_engine.corporate_action import (
    suspected_isin_change,
    suspected_merger,
    suspected_split_or_bonus,
)

_QTY_FLAT_TOLERANCE = 1e-6

# Expected days between consecutive disclosures, by cadence (spec §9.2: "Fortnightly vs
# monthly cadence" and "Late filings" / "Missing month in archive" hazards).
_EXPECTED_GAP_DAYS = {"monthly": 31, "fortnightly": 15, "halfyearly": 182}
_DEFAULT_EXPECTED_GAP_DAYS = 31
_GAP_TOLERANCE_MULTIPLIER = 1.5


def detect_disclosure_gap(
    prev_as_of: str, curr_as_of: str,
    prev_disclosure_type: str | None, curr_disclosure_type: str | None,
) -> str | None:
    """Compares the actual calendar gap between two compared snapshots against what their
    stated disclosure cadence implies. Returns a human-readable warning when the gap is
    wider than one ordinary filing period (a late or missing disclosure sits between them)
    or when the two snapshots don't share a cadence; returns None for an ordinary
    single-period gap. Purely advisory — never changes what diff_snapshots() computes, only
    what the caller says about it. Spec §9.2: "Report the gap explicitly. Do not
    interpolate. A 2-month change presented as a 1-month change is a lie."
    """
    try:
        prev_d = date.fromisoformat(prev_as_of)
        curr_d = date.fromisoformat(curr_as_of)
    except (TypeError, ValueError):
        return None
    actual_days = (curr_d - prev_d).days
    if actual_days <= 0:
        return None

    if prev_disclosure_type and curr_disclosure_type and prev_disclosure_type != curr_disclosure_type:
        return (f"Compared snapshots have different disclosure cadences "
                f"({prev_disclosure_type} -> {curr_disclosure_type}); the changes below span "
                "a cadence switch, not one ordinary filing period.")

    cadence = curr_disclosure_type or prev_disclosure_type
    expected = _EXPECTED_GAP_DAYS.get(cadence, _DEFAULT_EXPECTED_GAP_DAYS)
    if actual_days > expected * _GAP_TOLERANCE_MULTIPLIER:
        periods = round(actual_days / expected)
        return (f"{actual_days}-day gap between compared snapshots ({prev_as_of} -> "
                f"{curr_as_of}) is wider than one expected {cadence or 'monthly'} period "
                f"(~{expected}d) — this looks like roughly {periods} periods presented as a "
                "single comparison, likely a missed or late disclosure in between. The diff "
                "is not interpolated across it.")
    return None


@dataclass
class ChangeRow:
    isin: str | None
    name: str
    action: str  # NEW | EXITED | INCREASED | REDUCED | UNCHANGED | PRICE_FLOW_DRIFT
    delta_qty: float | None
    delta_pct_nav: float | None
    delta_value: float | None
    confidence: str  # "high" | "low"
    flags: list[str] = field(default_factory=list)


def diff_snapshots(prev_holdings: list, curr_holdings: list) -> list[ChangeRow]:
    """prev_holdings/curr_holdings: sqlite3.Row-like with isin, instrument_name, quantity,
    market_value_lakhs, pct_nav. Rows with isin=None (cash/derivatives/unlisted) are matched
    by instrument_name instead — a weaker key, always flagged low-confidence."""
    prev_by_key = {(h["isin"] or f"NAME::{h['instrument_name']}"): h for h in prev_holdings}
    curr_by_key = {(h["isin"] or f"NAME::{h['instrument_name']}"): h for h in curr_holdings}

    rows: list[ChangeRow] = []
    all_keys = set(prev_by_key) | set(curr_by_key)

    for key in all_keys:
        p = prev_by_key.get(key)
        c = curr_by_key.get(key)
        is_name_keyed = key.startswith("NAME::")

        if p is None and c is not None:
            rows.append(ChangeRow(
                isin=c["isin"], name=c["instrument_name"], action="NEW",
                delta_qty=c["quantity"], delta_pct_nav=c["pct_nav"], delta_value=c["market_value_lakhs"],
                confidence="low" if is_name_keyed else "high",
                flags=["name_keyed_no_isin"] if is_name_keyed else [],
            ))
            continue

        if p is not None and c is None:
            rows.append(ChangeRow(
                isin=p["isin"], name=p["instrument_name"], action="EXITED",
                delta_qty=(-p["quantity"] if p["quantity"] is not None else None),
                delta_pct_nav=(-p["pct_nav"] if p["pct_nav"] is not None else None),
                delta_value=(-p["market_value_lakhs"] if p["market_value_lakhs"] is not None else None),
                confidence="low" if is_name_keyed else "high",
                flags=["name_keyed_no_isin"] if is_name_keyed else [],
            ))
            continue

        # present in both
        pq, cq = p["quantity"], c["quantity"]
        p_pct, c_pct = p["pct_nav"], c["pct_nav"]
        p_val, c_val = p["market_value_lakhs"], c["market_value_lakhs"]
        delta_qty = (cq - pq) if (pq is not None and cq is not None) else None
        delta_pct = (c_pct - p_pct) if (p_pct is not None and c_pct is not None) else None
        delta_val = (c_val - p_val) if (p_val is not None and c_val is not None) else None

        flags: list[str] = []
        if is_name_keyed:
            flags.append("name_keyed_no_isin")

        if delta_qty is None:
            action = "UNCHANGED"
        elif abs(delta_qty) < _QTY_FLAT_TOLERANCE * max(abs(pq or 1), 1):
            if delta_pct is not None and abs(delta_pct) > 0.001:
                action = "PRICE_FLOW_DRIFT"
                flags.append("qty_flat_pct_nav_moved")
            else:
                action = "UNCHANGED"
        elif delta_qty > 0:
            action = "INCREASED"
        else:
            action = "REDUCED"

        if action in ("INCREASED", "REDUCED") and suspected_split_or_bonus(pq, cq, p_pct, c_pct):
            flags.append("corporate_action_suspected")

        rows.append(ChangeRow(
            isin=c["isin"], name=c["instrument_name"], action=action,
            delta_qty=delta_qty, delta_pct_nav=delta_pct, delta_value=delta_val,
            confidence="low" if flags else "high", flags=flags,
        ))

    # Cross-check EXITED/NEW pairs in the same batch for a possible ISIN change (near-identical
    # name -> same security under a new ISIN, spec §9.2) or merger (similar value, different
    # names -> restructuring). Name similarity is checked first and takes precedence: a rename
    # is a stronger, more specific explanation than "similar value" alone, and the two flags
    # would otherwise both fire for the common case of a straight ISIN reissue.
    exited = [r for r in rows if r.action == "EXITED"]
    new = [r for r in rows if r.action == "NEW"]
    for e in exited:
        for n in new:
            if suspected_isin_change(e.name, n.name):
                e.flags.append("isin_change_suspected")
                n.flags.append("isin_change_suspected")
                e.confidence = n.confidence = "low"
            elif suspected_merger(abs(e.delta_value or 0), abs(n.delta_value or 0)):
                e.flags.append("possible_corporate_action_merger")
                n.flags.append("possible_corporate_action_merger")
                e.confidence = n.confidence = "low"

    return rows
