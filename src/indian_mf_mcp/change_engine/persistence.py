"""Holding persistence / conviction streaks over a history of snapshots. Spec §5.2: "answers
5-year conviction holdings" — the most differentiated single output of the whole system.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PersistenceRow:
    isin: str | None
    name: str
    months_held: int
    continuous_streak_current: int
    avg_pct_nav: float
    first_seen_date: str
    last_seen_date: str


def compute_persistence(snapshots_holdings: list[tuple[str, list]]) -> list[PersistenceRow]:
    """snapshots_holdings: list of (as_of_date, holdings) tuples, ordered oldest -> newest.
    holdings rows expose isin, instrument_name, pct_nav. Cash/derivatives (isin=None,
    ambiguous name-keying) are included but will report low reliability implicitly via
    the caller filtering to equity-like asset classes before calling this."""
    by_key: dict[str, dict] = {}
    dates_seen: list[str] = [d for d, _ in snapshots_holdings]

    for as_of_date, holdings in snapshots_holdings:
        present_keys_this_month = set()
        for h in holdings:
            key = h["isin"] or f"NAME::{h['instrument_name']}"
            present_keys_this_month.add(key)
            entry = by_key.setdefault(key, {
                "isin": h["isin"], "name": h["instrument_name"],
                "pct_navs": [], "seen_dates": [],
            })
            entry["pct_navs"].append(h["pct_nav"] or 0.0)
            entry["seen_dates"].append(as_of_date)

    rows = []
    for key, entry in by_key.items():
        seen = sorted(entry["seen_dates"])
        months_held = len(seen)
        # current continuous streak: consecutive months up to the latest snapshot date
        streak = 0
        for d in reversed(dates_seen):
            if d in seen:
                streak += 1
            else:
                break
        avg_pct = sum(entry["pct_navs"]) / len(entry["pct_navs"]) if entry["pct_navs"] else 0.0
        rows.append(PersistenceRow(
            isin=entry["isin"], name=entry["name"], months_held=months_held,
            continuous_streak_current=streak, avg_pct_nav=avg_pct,
            first_seen_date=seen[0], last_seen_date=seen[-1],
        ))
    rows.sort(key=lambda r: r.continuous_streak_current, reverse=True)
    return rows
