import sqlite3

from indian_mf_mcp.change_engine.concentration import concentration_stats
from indian_mf_mcp.change_engine.corporate_action import (
    suspected_isin_change,
    suspected_merger,
    suspected_split_or_bonus,
)
from indian_mf_mcp.change_engine.persistence import compute_persistence
from indian_mf_mcp.change_engine.portfolio_diff import detect_disclosure_gap, diff_snapshots


def _row(isin, name, qty, value, pct):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (isin, instrument_name, quantity, market_value_lakhs, pct_nav)")
    conn.execute("INSERT INTO t VALUES (?, ?, ?, ?, ?)", (isin, name, qty, value, pct))
    return conn.execute("SELECT * FROM t").fetchone()


def test_new_and_exited_detected():
    prev = [_row("INE1", "Stock A", 1000, 100.0, 0.05)]
    curr = [_row("INE2", "Stock B", 500, 50.0, 0.02)]
    rows = {r.action: r for r in diff_snapshots(prev, curr)}
    assert rows["NEW"].isin == "INE2"
    assert rows["EXITED"].isin == "INE1"
    assert rows["EXITED"].delta_qty == -1000


def test_increased_and_reduced():
    prev = [_row("INE1", "Stock A", 1000, 100.0, 0.05)]
    curr = [_row("INE1", "Stock A", 1200, 130.0, 0.06)]
    rows = diff_snapshots(prev, curr)
    assert rows[0].action == "INCREASED"
    assert rows[0].delta_qty == 200


def test_price_flow_drift_when_qty_flat_but_pct_nav_moves():
    prev = [_row("INE1", "Stock A", 1000, 100.0, 0.05)]
    curr = [_row("INE1", "Stock A", 1000, 140.0, 0.07)]
    rows = diff_snapshots(prev, curr)
    assert rows[0].action == "PRICE_FLOW_DRIFT"
    assert "qty_flat_pct_nav_moved" in rows[0].flags


def test_split_flagged_not_asserted_as_trade():
    # 1:1 bonus -> qty doubles, %NAV roughly unchanged
    prev = [_row("INE1", "Stock A", 1000, 100.0, 0.05)]
    curr = [_row("INE1", "Stock A", 2000, 101.0, 0.0505)]
    rows = diff_snapshots(prev, curr)
    assert rows[0].action == "INCREASED"
    assert "corporate_action_suspected" in rows[0].flags
    assert rows[0].confidence == "low"


def test_suspected_split_helper_rejects_arbitrary_ratio():
    assert suspected_split_or_bonus(1000, 2000, 0.05, 0.0505) is True
    assert suspected_split_or_bonus(1000, 1730, 0.05, 0.0505) is False  # not a clean ratio


def test_suspected_merger_helper():
    assert suspected_merger(1000.0, 1050.0) is True
    assert suspected_merger(1000.0, 5000.0) is False


def test_concentration_stats():
    stats = concentration_stats([0.10, 0.08, 0.06, 0.05, 0.04, 0.03, 0.02])
    assert stats["n_holdings"] == 7
    assert round(stats["top5_pct"], 2) == 0.33
    assert stats["hhi"] > 0
    assert stats["effective_n"] > 0


def test_persistence_streak_across_months():
    m1 = [_row("INE1", "Stock A", 100, 10.0, 0.05)]
    m2 = [_row("INE1", "Stock A", 100, 11.0, 0.055)]
    m3 = [_row("INE2", "Stock B", 200, 5.0, 0.02)]  # A drops out in month 3
    rows = compute_persistence([("2026-01-31", m1), ("2026-02-28", m2), ("2026-03-31", m3)])
    by_isin = {r.isin: r for r in rows}
    assert by_isin["INE1"].months_held == 2
    assert by_isin["INE1"].continuous_streak_current == 0  # not present in latest month
    assert by_isin["INE2"].continuous_streak_current == 1


# ---------------------------------------------------------------------------
# ISIN-change / rename detection (spec §9.2)
# ---------------------------------------------------------------------------

def test_suspected_isin_change_helper_matches_near_identical_names():
    assert suspected_isin_change("HDFC Bank Limited", "HDFC Bank Ltd") is True
    assert suspected_isin_change("Reliance Industries Ltd", "Reliance Industries Limited") is True


def test_suspected_isin_change_helper_rejects_different_companies():
    assert suspected_isin_change("HDFC Bank Limited", "ICICI Bank Limited") is False


def test_suspected_isin_change_helper_handles_missing_names():
    assert suspected_isin_change(None, "HDFC Bank Ltd") is False
    assert suspected_isin_change("HDFC Bank Ltd", None) is False


def test_isin_change_flagged_over_exit_and_new_when_names_match():
    """An EXITED + NEW pair with near-identical names (same company, reissued ISIN) must be
    flagged as a suspected ISIN change, not left looking like an unrelated exit + fresh buy —
    and must not also collect the (weaker, less specific) merger flag."""
    prev = [_row("INE1OLD01", "HDFC Bank Limited", 1000, 100.0, 0.05)]
    curr = [_row("INE1NEW01", "HDFC Bank Ltd", 1000, 102.0, 0.051)]
    rows = {r.action: r for r in diff_snapshots(prev, curr)}
    assert "isin_change_suspected" in rows["EXITED"].flags
    assert "isin_change_suspected" in rows["NEW"].flags
    assert "possible_corporate_action_merger" not in rows["EXITED"].flags
    assert rows["EXITED"].confidence == "low"
    assert rows["NEW"].confidence == "low"


def test_merger_still_flagged_when_names_differ_but_values_match():
    """Two genuinely different companies (a real merger target) with similar market value
    and dissimilar names should still get the merger flag, not the rename flag."""
    prev = [_row("INE1", "Small Cap Target Ltd", 1000, 100.0, 0.05)]
    curr = [_row("INE2", "Acquiring Corp Ltd", 400, 105.0, 0.052)]
    rows = {r.action: r for r in diff_snapshots(prev, curr)}
    assert "possible_corporate_action_merger" in rows["EXITED"].flags
    assert "isin_change_suspected" not in rows["EXITED"].flags


# ---------------------------------------------------------------------------
# Missing-month / late-filing gap detection (spec §9.2)
# ---------------------------------------------------------------------------

def test_gap_detection_silent_for_ordinary_one_month_gap():
    assert detect_disclosure_gap("2026-07-31", "2026-08-31", "monthly", "monthly") is None


def test_gap_detection_flags_a_skipped_month():
    warning = detect_disclosure_gap("2026-06-30", "2026-08-31", "monthly", "monthly")
    assert warning is not None
    assert "62-day" in warning
    assert "2" in warning  # ~2 periods


def test_gap_detection_silent_for_ordinary_fortnightly_gap():
    assert detect_disclosure_gap("2026-08-15", "2026-08-31", "fortnightly", "fortnightly") is None


def test_gap_detection_flags_cadence_mismatch():
    warning = detect_disclosure_gap("2026-07-31", "2026-08-15", "monthly", "fortnightly")
    assert warning is not None
    assert "cadence" in warning.lower()


def test_gap_detection_handles_missing_dates_gracefully():
    assert detect_disclosure_gap(None, "2026-08-31", "monthly", "monthly") is None
    assert detect_disclosure_gap("not-a-date", "2026-08-31", "monthly", "monthly") is None
