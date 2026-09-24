"""Unit tests for Phase 3 manager extraction from factsheet text."""
from __future__ import annotations

import pytest

from indian_mf_mcp.ingest.manager_extract import (
    ExtractedManager,
    extract_managers_from_text,
    _parse_since_date,
)


class TestParseSinceDate:
    def test_dd_mon_yyyy(self):
        assert _parse_since_date("28-May-2013") == "2013-05-28"

    def test_month_day_year(self):
        assert _parse_since_date("May 28, 2013") == "2013-05-28"

    def test_unparseable_returns_raw(self):
        raw = "some date"
        assert _parse_since_date(raw) == raw


class TestExtractManagersFromText:

    def test_single_manager_inline_colon(self):
        text = "Fund Manager: Rajeev Thakkar\nSome other text here."
        results = extract_managers_from_text(text)
        assert len(results) == 1
        assert results[0].name == "Rajeev Thakkar"
        assert results[0].confidence == "high"

    def test_managed_by_label(self):
        text = "Managed by: Rajeev Thakkar"
        results = extract_managers_from_text(text)
        assert len(results) == 1
        assert results[0].name == "Rajeev Thakkar"

    def test_single_manager_with_managing_since(self):
        text = (
            "Fund Manager: Rajeev Thakkar "
            "(Managing since May 28, 2013)"
        )
        results = extract_managers_from_text(text)
        assert len(results) == 1
        assert results[0].name == "Rajeev Thakkar"
        assert results[0].managing_since == "2013-05-28"

    def test_no_match_returns_empty(self):
        text = "This text has no fund manager information."
        results = extract_managers_from_text(text)
        assert results == []

    def test_deduplication_same_name_twice(self):
        text = (
            "Fund Manager: Rajeev Thakkar\n"
            "Managed by: Rajeev Thakkar"
        )
        results = extract_managers_from_text(text)
        names = [r.name for r in results]
        assert names.count("Rajeev Thakkar") == 1

    def test_title_stripped(self):
        text = "Fund Manager: Mr. Rajeev Thakkar"
        results = extract_managers_from_text(text)
        assert len(results) == 1
        assert results[0].name == "Rajeev Thakkar"

    def test_noise_not_extracted(self):
        """Lowercase / numeric garbage should not produce manager hits."""
        text = "Fund Manager: xxxxxx 12345"
        results = extract_managers_from_text(text)
        assert results == []


def test_co_manager_list_wrapping_after_connector():
    """Kotak (Aug 2026) wraps the second co-manager onto the next line after '&'."""
    text = "Fund Manager*: Mr. Deepak Agrawal & \n Mr. Sunil Pandey\nAAUM:  41,044.12 crs"
    assert {m.name for m in extract_managers_from_text(text)} == {"Deepak Agrawal", "Sunil Pandey"}
