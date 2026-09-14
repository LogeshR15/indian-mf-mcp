"""Regression test for the reconciliation tolerance itself (spec §7: sum of "% to Net
Assets" must land within ~0.5% of 100, not the 1% the parser used before this test was
added). Uses a minimal synthetic workbook — real AMC fixtures all reconcile to within
0.01% of 100%, so they can't exercise the boundary directly.
"""
from __future__ import annotations

import io

import openpyxl
import pytest

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx


def _build_minimal_portfolio_xlsx(grand_total_pct: float) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name of the Instrument", "ISIN", "Industry", "Quantity",
               "Market Value (Rs. in Lakhs)", "% to Net Assets"])
    ws.append(["Equity & Equity Related", None, None, None, None, None])
    ws.append(["Test Corp Ltd", "INE000000001", "Banks", 100, 1000, grand_total_pct])
    ws.append(["Grand Total", None, None, None, 1000, grand_total_pct])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("pct", [1.0, 0.996, 1.004])
def test_within_half_percent_reconciles(pct):
    r = parse_portfolio_xlsx(_build_minimal_portfolio_xlsx(pct))
    assert r.reconciliation_ok is True
    assert r.parse_confidence == 1.0


@pytest.mark.parametrize("pct", [0.994, 1.006, 0.98, 1.02])
def test_beyond_half_percent_is_flagged_not_silently_served(pct):
    r = parse_portfolio_xlsx(_build_minimal_portfolio_xlsx(pct))
    assert r.reconciliation_ok is False
    assert r.parse_confidence == 0.5


def test_a_deviation_that_used_to_pass_under_the_old_one_percent_tolerance_now_fails():
    """0.6% off of 100% used to reconcile under the old `<= 0.01` check; the tightened
    `<= 0.005` tolerance must now flag it. Pins the exact regression this test exists for."""
    r = parse_portfolio_xlsx(_build_minimal_portfolio_xlsx(0.994))
    assert r.reconciliation_ok is False
