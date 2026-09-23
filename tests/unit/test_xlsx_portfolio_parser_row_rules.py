"""Row-classification and reconciliation rules in the shared parser, each pinned on a minimal
workbook shaped like the real AMC file that exposed it. The golden-fixture tests for those AMCs
(quant, Nippon, ICICI Prudential, Franklin, Baroda BNP Paribas) cover the same rules on the
real files; these isolate one rule per test so a regression names its cause.
"""
from __future__ import annotations

import io

import openpyxl
import pytest

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

_HEADER = ["Name of the Instrument", "ISIN", "Industry", "Quantity",
           "Market Value (Rs. in Lakhs)", "% to Net Assets"]


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _names(r) -> list[str]:
    return [h.instrument_name for h in r.holdings]


def test_one_word_subtotal_is_not_a_holding():
    """Nippon India prints "Subtotal" as one word; it was loaded as a ~99% fake holding."""
    r = parse_portfolio_xlsx(_xlsx([
        _HEADER,
        ["Equity & Equity Related", None, None, None, None, None],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 10, 600.0, 0.6],
        ["Infosys Limited", "INE009A01021", "IT - Software", 10, 400.0, 0.4],
        ["Subtotal", None, None, None, 1000.0, 1.0],
        ["Grand Total", None, None, None, 1000.0, 1.0],
    ]))
    assert "Subtotal" not in _names(r)
    assert r.reconciliation_ok is True


def test_company_named_total_is_kept():
    """"Adani Total Gas Ltd." carries an ISIN — the aggregate-label skip must not drop it."""
    r = parse_portfolio_xlsx(_xlsx([
        _HEADER,
        ["Equity & Equity Related", None, None, None, None, None],
        ["Adani Total Gas Ltd.", "INE399L01023", "Gas", 10, 200.0, 0.2],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 10, 800.0, 0.8],
        ["Sub Total", None, None, None, 1000.0, 1.0],
        ["Grand Total", None, None, None, 1000.0, 1.0],
    ]))
    assert "Adani Total Gas Ltd." in _names(r)
    assert r.reconciliation_ok is True


def test_reconciliation_fails_when_extracted_rows_do_not_sum_to_grand_total():
    """The file's own GRAND TOTAL = 100% is not enough: a dropped section must fail."""
    r = parse_portfolio_xlsx(_xlsx([
        _HEADER,
        ["Equity & Equity Related", None, None, None, None, None],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 10, 700.0, 0.7],
        ["Grand Total", None, None, None, 1000.0, 1.0],
    ]))
    assert r.reconciliation_ok is False
    assert any("extracted rows sum to" in w for w in r.warnings)


def test_sections_after_derivatives_are_holdings_again():
    """quant / Baroda BNP Paribas: futures printed as main-table rows, then MONEY MARKET and
    OTHERS. Futures go to derivatives with main-table columns; what follows is holdings."""
    r = parse_portfolio_xlsx(_xlsx([
        _HEADER,
        ["Equity & Equity Related", None, None, None, None, None],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 10, 700.0, 0.7],
        ["Sub Total", None, None, None, 700.0, 0.7],
        ["Derivatives", None, None, None, None, None],
        ["(a) Index / Stock Futures", None, None, None, None, None],
        ["TCS Limited 29/09/2026", "TCS290926", "IT - Software", 500, 250.0, 0.25],
        ["Sub Total", None, None, None, 250.0, 0.25],
        ["Money Market Instruments", None, None, None, None, None],
        ["91 Days Treasury Bill", "IN002026X099", "SOV", 100, 100.0, 0.1],
        ["Others", None, None, None, None, None],
        ["TREPS", None, None, None, 200.0, 0.2],
        ["Net Current Assets", None, None, None, -250.0, -0.25],
        ["Grand Total", None, None, None, 1000.0, 1.0],
    ]))
    assert {"91 Days Treasury Bill", "TREPS", "Net Current Assets"} <= set(_names(r))
    [fut] = r.derivatives
    assert fut.instrument_name == "TCS Limited 29/09/2026"
    assert fut.market_value_lakhs == pytest.approx(250.0)
    assert fut.pct_to_aum == pytest.approx(0.25)
    assert r.reconciliation_ok is True  # holdings 0.75 + futures 0.25 = grand total


def test_industry_preferred_over_separate_rating_column():
    """quant lists RATING ("N.A." for equities) before INDUSTRY."""
    header = ["ISIN", "Name of the Instrument", "Rating", "Industry", "Quantity",
              "Market Value (Rs. in Lakhs)", "% to NAV"]
    r = parse_portfolio_xlsx(_xlsx([
        header,
        ["", "Equity & Equity Related", "", "", "", "", ""],
        ["INE040A01034", "HDFC Bank Limited", "N.A.", "Banks", 10, 900.0, 0.9],
        ["IN002026X099", "91 Days Treasury Bill", "SOV", "N.A.", 100, 100.0, 0.1],
        ["", "Grand Total", "", "", "", 1000.0, 1.0],
    ]))
    by_name = {h.instrument_name: h.industry_or_rating for h in r.holdings}
    assert by_name["HDFC Bank Limited"] == "Banks"
    assert by_name["91 Days Treasury Bill"] == "SOV"


def test_header_carrying_its_own_subtotal_is_dropped_but_standalone_line_kept():
    """ICICI Prudential: "Treasury Bills 0.3" above the bills themselves is a header; "TREPS
    0.1" with nothing under it is a real holding. Same shape; only the children tell them apart."""
    r = parse_portfolio_xlsx(_xlsx([
        _HEADER,
        ["Equity & Equity Related Instruments", "", "", "", 600.0, 0.6],
        ["Listed / Awaiting Listing On Stock Exchanges", "", "", "", 600.0, 0.6],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 10, 600.0, 0.6],
        ["Money Market Instruments", "", "", "", 300.0, 0.3],
        ["Treasury Bills", "", "", "", 300.0, 0.3],
        ["364 Days Treasury Bills", "IN002025Z336", "SOV", 10, 200.0, 0.2],
        ["91 Days Treasury Bills", "IN002026X131", "SOV", 10, 100.0, 0.1],
        ["TREPS", "", "", "", 100.0, 0.1],
        ["Total Net Assets", "", "", "", 1000.0, 1.0],
    ]))
    names = _names(r)
    assert "Treasury Bills" not in names
    assert "Listed / Awaiting Listing On Stock Exchanges" not in names
    assert "TREPS" in names
    hdfc = next(h for h in r.holdings if h.instrument_name == "HDFC Bank Limited")
    assert hdfc.listed is True
    assert r.reconciliation_ok is True


def test_valued_line_mentioning_a_section_keyword_is_a_holding():
    """Nippon's "Cash Margin - Derivatives 0.0051" is not a "derivatives" section header."""
    r = parse_portfolio_xlsx(_xlsx([
        _HEADER,
        ["Equity & Equity Related", None, None, None, None, None],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 10, 995.0, 0.995],
        ["Others", None, None, None, None, None],
        ["Cash Margin - Derivatives", None, None, None, 5.0, 0.005],
        ["Grand Total", None, None, None, 1000.0, 1.0],
    ]))
    assert "Cash Margin - Derivatives" in _names(r)
    assert r.reconciliation_ok is True


def test_nothing_after_grand_total_becomes_a_holding():
    """Franklin prints an IRS notional table and NAV-per-plan rows after GRAND TOTAL."""
    r = parse_portfolio_xlsx(_xlsx([
        _HEADER,
        ["Debt Instruments", None, None, None, None, None],
        ["7.38% REC Ltd", "INE020B08GD4", "CRISIL AAA", 10, 1000.0, 1.0],
        ["Grand Total", None, None, None, 1000.0, 1.0],
        ["DBS BANK LTD (Pay Fixed - Receive Floating)", None, None, None, 44.0, 0.044],
        ["Growth Plan", None, None, None, None, None],
    ]))
    assert _names(r) == ["7.38% REC Ltd"]
    assert r.reconciliation_ok is True


def test_nil_placeholder_section_is_not_a_holding():
    r = parse_portfolio_xlsx(_xlsx([
        _HEADER,
        ["Equity & Equity Related", None, None, None, None, None],
        ["HDFC Bank Limited", "INE040A01034", "Banks", 10, 1000.0, 1.0],
        ["Unlisted", "", "", "", "Nil", "Nil"],
        ["Grand Total", None, None, None, 1000.0, 1.0],
    ]))
    assert _names(r) == ["HDFC Bank Limited"]
