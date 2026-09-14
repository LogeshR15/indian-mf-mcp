"""Unit tests for ingest/amc_adapters/generic_fallback.py.

This adapter is not wired into registry.py yet (see its module docstring — it needs a
per-AMC registry_url that nothing currently supplies automatically), but it should still
work correctly and be covered by tests so it doesn't bit-rot while dormant. These tests
also cover a real bug found while auditing it: `_DATE_HINTS` was compiled but never
actually used, so every DocumentRef's `as_of_date` was hardcoded to None regardless of
whether the filename had a parseable date.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from indian_mf_mcp.ingest.amc_adapters.base import DocType
from indian_mf_mcp.ingest.amc_adapters.generic_fallback import (
    GenericFallbackAdapter,
    _date_hint_from_filename,
)


class TestDateHintFromFilename:
    def test_iso_style_date(self):
        assert _date_hint_from_filename("Portfolio_2026-08-31.xlsx") == date(2026, 8, 31)

    def test_compact_date(self):
        assert _date_hint_from_filename("portfolio_20260831.xlsx") == date(2026, 8, 31)

    def test_month_name_style(self):
        assert _date_hint_from_filename("Monthly-Portfolio-Aug-31-2026.pdf") == date(2026, 8, 31)

    def test_no_date_returns_none(self):
        assert _date_hint_from_filename("monthly-portfolio-disclosure.xlsx") is None

    def test_invalid_date_components_return_none_not_raise(self):
        # 13th month — matches the numeric pattern but isn't a real date.
        assert _date_hint_from_filename("report_2026-13-99.xlsx") is None


class TestGenericFallbackAdapterListDocuments:
    HTML = """
    <html><body>
    <a href="/downloads/Portfolio_2026-08-31.xlsx">August 2026</a>
    <a href="/downloads/Portfolio_2026-07-31.xlsx">July 2026</a>
    <a href="/downloads/factsheet.pdf">Undated factsheet</a>
    <a href="/about-us">Not a document link</a>
    <a href="https://cdn.example-amc.com/files/Report-Sep-15-2026.pdf">Absolute link</a>
    </body></html>
    """

    def test_extracts_all_matching_links_with_resolved_urls_and_dates(self):
        adapter = GenericFallbackAdapter(
            amc_id="example-amc", registry_url="https://example-amc.com/disclosures"
        )
        with patch(
            "indian_mf_mcp.ingest.amc_adapters.generic_fallback.http_get",
            return_value=self.HTML.encode("utf-8"),
        ):
            refs = adapter.list_documents(DocType.MONTHLY_PORTFOLIO, since=date(2026, 1, 1))

        assert len(refs) == 4  # the "/about-us" link must not match

        by_date = {r.as_of_date: r for r in refs}
        assert by_date[date(2026, 8, 31)].url == \
            "https://example-amc.com/downloads/Portfolio_2026-08-31.xlsx"
        assert by_date[date(2026, 7, 31)].url == \
            "https://example-amc.com/downloads/Portfolio_2026-07-31.xlsx"
        assert by_date[date(2026, 9, 15)].url == \
            "https://cdn.example-amc.com/files/Report-Sep-15-2026.pdf"

        undated = [r for r in refs if r.as_of_date is None]
        assert len(undated) == 1
        assert undated[0].url == "https://example-amc.com/downloads/factsheet.pdf"

    def test_no_matching_links_returns_empty_list(self):
        adapter = GenericFallbackAdapter(
            amc_id="example-amc", registry_url="https://example-amc.com/disclosures"
        )
        with patch(
            "indian_mf_mcp.ingest.amc_adapters.generic_fallback.http_get",
            return_value=b"<html><body><a href='/about-us'>No docs here</a></body></html>",
        ):
            refs = adapter.list_documents(DocType.MONTHLY_PORTFOLIO, since=date(2026, 1, 1))
        assert refs == []


class TestGenericFallbackAdapterFetch:
    def test_fetch_delegates_to_http_get(self):
        adapter = GenericFallbackAdapter(
            amc_id="example-amc", registry_url="https://example-amc.com/disclosures"
        )
        from indian_mf_mcp.ingest.amc_adapters.base import DocumentRef

        ref = DocumentRef(
            url="https://example-amc.com/downloads/Portfolio_2026-08-31.xlsx",
            doc_type=DocType.MONTHLY_PORTFOLIO,
            as_of_date=date(2026, 8, 31),
        )
        with patch(
            "indian_mf_mcp.ingest.amc_adapters.generic_fallback.http_get",
            return_value=b"PK\x03\x04fake-xlsx-bytes",
        ) as mock_get:
            raw = adapter.fetch(ref)
        assert raw == b"PK\x03\x04fake-xlsx-bytes"
        mock_get.assert_called_once_with(ref.url, client=None)
