"""Unit tests for Phase 3 benchmark proxy resolution."""
from __future__ import annotations

import pytest

from indian_mf_mcp.analytics.benchmark import (
    BENCHMARK_PROXIES,
    _match_proxy,
    benchmark_cagr,
    benchmark_daily_returns,
)
from indian_mf_mcp.analytics.returns import NavSeries
from datetime import date


class TestMatchProxy:
    def test_nifty50_exact(self):
        entry = _match_proxy("Nifty 50 TRI")
        assert entry is not None
        assert "nifty 50" in entry["label"].lower()

    def test_nifty500_substring(self):
        entry = _match_proxy("AMFI Tier 1 Benchmark: Nifty 500 TRI")
        assert entry is not None

    def test_sensex_match(self):
        entry = _match_proxy("BSE Sensex TRI")
        assert entry is not None

    def test_no_match_returns_none(self):
        entry = _match_proxy("Unknown Index XYZ")
        assert entry is None

    def test_none_benchmark_name(self):
        entry = _match_proxy(None)
        assert entry is None

    def test_all_registered_proxies_have_required_keys(self):
        for key, entry in BENCHMARK_PROXIES.items():
            assert "scheme_code" in entry, f"{key} missing scheme_code"
            assert "label" in entry, f"{key} missing label"
            assert "caveat" in entry, f"{key} missing caveat"


class TestBenchmarkMetrics:
    @pytest.fixture
    def proxy_with_series(self):
        from datetime import timedelta
        start = date(2020, 1, 1)
        dates = [start + timedelta(days=i) for i in range(100)]
        navs = [100.0 + i * 0.5 for i in range(100)]
        series = NavSeries(dates, navs)
        return {
            "label": "Test Proxy",
            "plan_id": "plan-test",
            "series": series,
            "caveat": "test proxy",
            "comparator_type": "proxy",
        }

    @pytest.fixture
    def proxy_without_series(self):
        return {
            "label": "No Data Proxy",
            "plan_id": None,
            "series": None,
            "caveat": "not ingested",
            "comparator_type": "proxy",
        }

    def test_benchmark_cagr_with_series(self, proxy_with_series):
        result = benchmark_cagr(
            proxy_with_series,
            start=date(2020, 1, 1),
            end=date(2020, 3, 1),
        )
        assert result is not None
        assert isinstance(result, float)

    def test_benchmark_cagr_without_series_returns_none(self, proxy_without_series):
        result = benchmark_cagr(
            proxy_without_series,
            start=date(2020, 1, 1),
            end=date(2020, 3, 1),
        )
        assert result is None

    def test_benchmark_daily_returns_with_series(self, proxy_with_series):
        rets = benchmark_daily_returns(proxy_with_series)
        assert isinstance(rets, list)
        assert len(rets) > 0

    def test_benchmark_daily_returns_without_series_empty(self, proxy_without_series):
        rets = benchmark_daily_returns(proxy_without_series)
        assert rets == []


class TestTERExtraction:
    """Test TER extraction from factsheet text."""

    def test_ter_direct_extracted(self):
        from indian_mf_mcp.ingest.factsheet_ingest import extract_ter_from_pages

        class FakePage:
            text = "Total Expense Ratio (TER): Direct Plan: 0.63%  Regular Plan: 1.31%"

        entries = extract_ter_from_pages([FakePage()])
        assert len(entries) == 2
        direct = next((e for e in entries if e.plan_type == "Direct"), None)
        regular = next((e for e in entries if e.plan_type == "Regular"), None)
        assert direct is not None
        assert abs(direct.ter_pct - 0.63) < 1e-9
        assert regular is not None
        assert abs(regular.ter_pct - 1.31) < 1e-9

    def test_ter_not_in_text_returns_empty(self):
        from indian_mf_mcp.ingest.factsheet_ingest import extract_ter_from_pages

        class FakePage:
            text = "No expense information here at all."

        entries = extract_ter_from_pages([FakePage()])
        assert entries == []

    def test_ter_direct_only(self):
        from indian_mf_mcp.ingest.factsheet_ingest import extract_ter_from_pages

        class FakePage:
            text = "Expense Ratio: Direct: 0.51%"

        entries = extract_ter_from_pages([FakePage()])
        direct = next((e for e in entries if e.plan_type == "Direct"), None)
        assert direct is not None
        assert abs(direct.ter_pct - 0.51) < 1e-9
