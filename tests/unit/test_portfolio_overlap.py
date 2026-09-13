"""Unit tests for analytics/overlap.py — portfolio overlap computation."""
from __future__ import annotations

import pytest
from indian_mf_mcp.analytics.overlap import (
    portfolio_overlap,
    all_pairs_overlap,
    _equity_isin_map,
)


def _h(isin, name, pct, asset_class="equity"):
    """Helper: build a minimal holding dict."""
    return {
        "isin": isin,
        "instrument_name": name,
        "pct_nav": pct,
        "asset_class": asset_class,
    }


class TestEquityIsinMap:
    def test_basic(self):
        holdings = [_h("INE001", "Foo", 10.0), _h("INE002", "Bar", 5.0)]
        m = _equity_isin_map(holdings)
        assert set(m) == {"INE001", "INE002"}
        assert m["INE001"]["pct_nav"] == 10.0

    def test_excludes_cash_and_debt(self):
        holdings = [
            _h("INE001", "Equity Co", 10.0, asset_class="equity"),
            _h("INE002", "Debt Bond", 5.0, asset_class="debt"),
            _h(None, "TREPS", 2.0, asset_class="cash"),
        ]
        m = _equity_isin_map(holdings)
        assert "INE001" in m
        assert "INE002" not in m   # debt excluded
        assert None not in m       # no-isin excluded

    def test_includes_foreign_and_reit(self):
        holdings = [
            _h("US123456789A", "Foreign Co", 8.0, asset_class="foreign"),
            _h("INE999", "REIT Co", 3.0, asset_class="reit"),
        ]
        m = _equity_isin_map(holdings)
        assert "US123456789A" in m
        assert "INE999" in m

    def test_deduplicates_same_isin(self):
        holdings = [
            _h("INE001", "Foo", 5.0),
            _h("INE001", "Foo Listed", 3.0),  # same ISIN, different sub-section
        ]
        m = _equity_isin_map(holdings)
        assert len(m) == 1
        assert m["INE001"]["pct_nav"] == pytest.approx(8.0)

    def test_null_isin_skipped(self):
        holdings = [_h(None, "Unlisted", 5.0)]
        m = _equity_isin_map(holdings)
        assert len(m) == 0


class TestPortfolioOverlap:
    def _make_holdings(self, entries):
        return [_h(isin, name, pct) for isin, name, pct in entries]

    def test_no_overlap(self):
        a = self._make_holdings([("INE001", "Alpha", 30.0), ("INE002", "Beta", 20.0)])
        b = self._make_holdings([("INE003", "Gamma", 40.0), ("INE004", "Delta", 25.0)])
        result = portfolio_overlap(a, b, "FUND_A", "FUND_B")
        assert result.overlap_pct_a == 0.0
        assert result.overlap_pct_b == 0.0
        assert result.common_count == 0
        assert result.common_holdings == []

    def test_full_overlap(self):
        holdings = self._make_holdings([("INE001", "Alpha", 30.0), ("INE002", "Beta", 20.0)])
        result = portfolio_overlap(holdings, holdings, "FUND_A", "FUND_A_COPY")
        assert result.overlap_pct_a == pytest.approx(50.0)
        assert result.overlap_pct_b == pytest.approx(50.0)
        assert result.common_count == 2

    def test_partial_overlap(self):
        a = self._make_holdings([("INE001", "Alpha", 30.0), ("INE002", "Beta", 20.0)])
        b = self._make_holdings([("INE001", "Alpha", 10.0), ("INE003", "Gamma", 25.0)])
        result = portfolio_overlap(a, b, "FUND_A", "FUND_B")
        # A has INE001 at 30% — overlap from A's perspective = 30
        assert result.overlap_pct_a == pytest.approx(30.0)
        # B has INE001 at 10% — overlap from B's perspective = 10
        assert result.overlap_pct_b == pytest.approx(10.0)
        assert result.common_count == 1
        assert result.common_holdings[0]["isin"] == "INE001"

    def test_asymmetry(self):
        """A large fund with 5% in a stock vs a small fund with 40% in the same stock."""
        a = self._make_holdings([("INE001", "HDFC Bank", 5.0), ("INE002", "Other", 60.0)])
        b = self._make_holdings([("INE001", "HDFC Bank", 40.0), ("INE003", "Niche", 30.0)])
        result = portfolio_overlap(a, b, "LARGE", "SMALL")
        assert result.overlap_pct_a == pytest.approx(5.0)
        assert result.overlap_pct_b == pytest.approx(40.0)
        assert result.overlap_pct_avg == pytest.approx(22.5)

    def test_common_holdings_sorted_by_avg(self):
        a = self._make_holdings([
            ("INE001", "Big", 30.0), ("INE002", "Small", 5.0), ("INE003", "Med", 15.0),
        ])
        b = self._make_holdings([
            ("INE001", "Big", 25.0), ("INE002", "Small", 8.0), ("INE003", "Med", 12.0),
        ])
        result = portfolio_overlap(a, b, "A", "B")
        # Sorted by avg_pct_nav descending: INE001 (27.5), INE003 (13.5), INE002 (6.5)
        assert result.common_holdings[0]["isin"] == "INE001"
        assert result.common_holdings[1]["isin"] == "INE003"
        assert result.common_holdings[2]["isin"] == "INE002"

    def test_scheme_ids_recorded(self):
        a = self._make_holdings([("INE001", "A", 10.0)])
        b = self._make_holdings([("INE001", "A", 10.0)])
        result = portfolio_overlap(a, b, "SCHEME_X", "SCHEME_Y")
        assert result.scheme_id_a == "SCHEME_X"
        assert result.scheme_id_b == "SCHEME_Y"

    def test_empty_holdings(self):
        result = portfolio_overlap([], [], "A", "B")
        assert result.overlap_pct_a == 0.0
        assert result.common_count == 0


class TestAllPairsOverlap:
    def test_two_schemes(self):
        holdings = {
            "A": [_h("INE001", "X", 20.0)],
            "B": [_h("INE001", "X", 15.0), _h("INE002", "Y", 10.0)],
        }
        results = all_pairs_overlap(holdings)
        assert len(results) == 1
        assert results[0].overlap_pct_a == pytest.approx(20.0)

    def test_three_schemes_three_pairs(self):
        holdings = {
            "A": [_h("INE001", "X", 20.0)],
            "B": [_h("INE001", "X", 15.0)],
            "C": [_h("INE002", "Y", 30.0)],
        }
        results = all_pairs_overlap(holdings)
        assert len(results) == 3  # C(3,2) = 3 pairs

    def test_single_scheme_no_pairs(self):
        holdings = {"A": [_h("INE001", "X", 20.0)]}
        results = all_pairs_overlap(holdings)
        assert results == []

    def test_pairs_are_lexically_ordered(self):
        """Pairs should be (A,B), (A,C), (B,C) not (B,A)."""
        holdings = {
            "FUND_C": [_h("INE001", "X", 10.0)],
            "FUND_A": [_h("INE001", "X", 10.0)],
            "FUND_B": [_h("INE001", "X", 10.0)],
        }
        results = all_pairs_overlap(holdings)
        # All have the same ISIN so all overlap, 3 pairs
        assert len(results) == 3
        # First pair should be FUND_A vs FUND_B (lexically smallest)
        first = results[0]
        assert first.scheme_id_a == "FUND_A"
        assert first.scheme_id_b == "FUND_B"
