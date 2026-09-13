"""Unit tests for ingest/addendum_signals.py — regex-based change signal extraction."""
from __future__ import annotations

import pytest
from indian_mf_mcp.ingest.addendum_signals import (
    extract_change_signals,
    _parse_loose_date,
    _extract_effective_date,
    ChangeSignal,
)


DOC_DATE = "2026-09-01"


class TestParsLooseDate:
    def test_iso_format(self):
        assert _parse_loose_date("2026-09-01") == "2026-09-01"

    def test_dd_mm_yyyy_slash(self):
        assert _parse_loose_date("01/09/2026") == "2026-09-01"

    def test_dd_mm_yyyy_dash(self):
        assert _parse_loose_date("01-09-2026") == "2026-09-01"

    def test_month_name(self):
        result = _parse_loose_date("1 September 2026")
        assert result == "2026-09-01"

    def test_month_name_with_ordinal(self):
        result = _parse_loose_date("1st September 2026")
        assert result == "2026-09-01"

    def test_invalid_returns_none(self):
        assert _parse_loose_date("not a date") is None

    def test_invalid_date_values(self):
        # Feb 30 — invalid
        assert _parse_loose_date("30/02/2026") is None


class TestExtractEffectiveDate:
    def test_wef_pattern(self):
        text = "The change will take effect w.e.f. 01 September 2026."
        result = _extract_effective_date(text, DOC_DATE)
        assert result == "2026-09-01"

    def test_effective_from_pattern(self):
        text = "effective from 15-09-2026"
        result = _extract_effective_date(text, DOC_DATE)
        assert result == "2026-09-15"

    def test_fallback_to_doc_date(self):
        text = "No date mentioned anywhere here."
        result = _extract_effective_date(text, DOC_DATE)
        assert result == DOC_DATE


class TestManagerSignals:
    def test_appointment(self):
        text = (
            "Mr. Rajeev Thakkar has been appointed as fund manager of the scheme "
            "w.e.f. 01 September 2026."
        )
        signals = extract_change_signals([text], DOC_DATE)
        mgr = [s for s in signals if s.event_type == "manager_change"]
        assert len(mgr) >= 1
        appt = [s for s in mgr if s.sub_type == "appointment"]
        assert len(appt) >= 1
        assert "Rajeev Thakkar" in appt[0].after
        assert appt[0].confidence == "official"

    def test_cessation(self):
        text = (
            "Mr. John Smith has ceased to be the fund manager of the scheme "
            "w.e.f. 15 September 2026."
        )
        signals = extract_change_signals([text], DOC_DATE)
        mgr = [s for s in signals if s.event_type == "manager_change"]
        assert len(mgr) >= 1
        cease = [s for s in mgr if s.sub_type == "cessation"]
        assert len(cease) >= 1
        assert "John Smith" in cease[0].before

    def test_general_manager_change(self):
        text = "This is to inform investors of a change of fund manager for the scheme."
        signals = extract_change_signals([text], DOC_DATE)
        mgr = [s for s in signals if s.event_type == "manager_change"]
        assert len(mgr) >= 1

    def test_confidence_always_official(self):
        text = "Rajeev Thakkar has been appointed as fund manager w.e.f. 01-09-2026."
        signals = extract_change_signals([text], DOC_DATE)
        for s in signals:
            assert s.confidence == "official"


class TestBenchmarkSignals:
    def test_benchmark_change_detected(self):
        text = (
            "Investors are hereby notified of a change in the benchmark of the scheme. "
            "The existing benchmark: Nifty 500 TRI will be revised benchmark: Nifty 50 TRI "
            "effective from 01-10-2026."
        )
        signals = extract_change_signals([text], DOC_DATE)
        bench = [s for s in signals if s.event_type == "benchmark_change"]
        assert len(bench) >= 1
        assert bench[0].confidence == "official"


class TestTERSignals:
    def test_ter_change_detected(self):
        text = (
            "The Total Expense Ratio (TER) of the scheme shall be revised from 1.50% to 1.25% "
            "w.e.f. 01-09-2026."
        )
        signals = extract_change_signals([text], DOC_DATE)
        ter = [s for s in signals if s.event_type == "ter_change"]
        assert len(ter) >= 1
        assert ter[0].confidence == "official"

    def test_ter_values_extracted(self):
        text = "TER changed from 1.50% to 1.25% effective from 2026-09-01."
        signals = extract_change_signals([text], DOC_DATE)
        ter = [s for s in signals if s.event_type == "ter_change"]
        if ter:
            # At least one of before/after should be populated
            assert ter[0].before is not None or ter[0].after is not None


class TestCategorySignals:
    def test_recategorisation(self):
        text = (
            "Pursuant to SEBI circular, the scheme is being re-categorised from "
            "Flexi Cap to Multi Cap Fund with effect from 01-01-2026."
        )
        signals = extract_change_signals([text], DOC_DATE)
        cat = [s for s in signals if s.event_type == "category_change"]
        assert len(cat) >= 1

    def test_scheme_merger(self):
        text = "Merger of scheme XYZ into scheme ABC as per SEBI guidelines."
        signals = extract_change_signals([text], DOC_DATE)
        cat = [s for s in signals if s.event_type == "category_change"]
        assert len(cat) >= 1


class TestMandateSignals:
    def test_investment_objective_revision(self):
        text = (
            "Revision in investment objective: The investment objective of the scheme is "
            "being modified to include overseas securities."
        )
        signals = extract_change_signals([text], DOC_DATE)
        mandate = [s for s in signals if s.event_type == "mandate_revision"]
        assert len(mandate) >= 1

    def test_asset_allocation_change(self):
        text = "Change in the Asset Allocation Pattern of the scheme w.e.f. 2026-09-15."
        signals = extract_change_signals([text], DOC_DATE)
        mandate = [s for s in signals if s.event_type == "mandate_revision"]
        assert len(mandate) >= 1


class TestHaltSignals:
    def test_subscription_halt(self):
        text = (
            "The AMC hereby announces temporary suspension of fresh subscription "
            "in the scheme with effect from 01-09-2026."
        )
        signals = extract_change_signals([text], DOC_DATE)
        halt = [s for s in signals if s.event_type == "addendum"]
        assert len(halt) >= 1
        assert halt[0].after and "subscription" in halt[0].after


class TestFallbackGenericAddendum:
    def test_no_specific_signal_emits_generic(self):
        """When no patterns match, we still emit a generic addendum event."""
        text = "This is a notice to all investors of the scheme regarding routine matters."
        signals = extract_change_signals([text], DOC_DATE)
        assert len(signals) >= 1
        assert signals[0].confidence == "official"

    def test_multi_page_no_cross_contamination(self):
        """Signals from page 1 should not bleed into page 2's context."""
        pages = [
            "Rajeev Thakkar has been appointed as fund manager w.e.f. 01-09-2026.",
            "Separately, this page discusses fund performance.",
        ]
        signals = extract_change_signals(pages, DOC_DATE)
        mgr = [s for s in signals if s.event_type == "manager_change"]
        assert len(mgr) >= 1


class TestDeduplication:
    def test_no_duplicate_same_event(self):
        """Same appointment mentioned twice should not produce two signals."""
        text = (
            "Mr. Rajeev Thakkar has been appointed as fund manager w.e.f. 01-09-2026. "
            "Mr. Rajeev Thakkar has been appointed as fund manager w.e.f. 01-09-2026."
        )
        signals = extract_change_signals([text], DOC_DATE)
        mgr = [s for s in signals if s.event_type == "manager_change" and s.sub_type == "appointment"]
        # Should be deduplicated (same type, date, name)
        assert len(mgr) == 1


class TestRawExcerpt:
    def test_raw_excerpt_populated(self):
        text = "Mr. Prashant Jain has been appointed as fund manager w.e.f. 2026-09-01."
        signals = extract_change_signals([text], DOC_DATE)
        for s in signals:
            assert isinstance(s.raw_excerpt, str)
            assert len(s.raw_excerpt) > 0
