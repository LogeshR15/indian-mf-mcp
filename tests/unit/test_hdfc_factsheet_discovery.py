"""HDFC monthly factsheet discovery: URL construction and probing, no network."""
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType
from indian_mf_mcp.ingest.amc_adapters.hdfc import HDFCAdapter, factsheet_candidate_urls

BASE = "https://files.hdfcfund.com/s3fs-public/"


def test_candidate_urls_order_and_encoding():
    urls = factsheet_candidate_urls(date(2026, 8, 31))
    assert urls[0] == BASE + "2026-09/HDFC%20MF%20Factsheet%20-%20August%202026.pdf"
    assert urls[1] == BASE + "2026-09/HDFC%20MF%20Factsheet%20August%202026.pdf"
    assert urls[2] == BASE + "2026-09/HDFC%20MF%20Factsheet%20-%20August%202026%20%281%29.pdf"
    assert urls[3] == BASE + "2026-10/HDFC%20MF%20Factsheet%20-%20August%202026.pdf"
    assert len(urls) == 6


def test_candidate_urls_roll_over_year_end():
    urls = factsheet_candidate_urls(date(2025, 12, 31))
    assert urls[0] == BASE + "2026-01/HDFC%20MF%20Factsheet%20-%20December%202025.pdf"
    assert urls[3] == BASE + "2026-02/HDFC%20MF%20Factsheet%20-%20December%202025.pdf"


def test_list_factsheets_probes_variants_and_skips_gaps():
    present = {
        # June: canonical name.
        BASE + "2026-07/HDFC%20MF%20Factsheet%20-%20June%202026.pdf",
        # July: only the dash-less variant exists.
        BASE + "2026-08/HDFC%20MF%20Factsheet%20July%202026.pdf",
        # August: absent everywhere -> skipped, never guessed.
    }
    seen_uas = set()

    def handler(request: httpx.Request) -> httpx.Response:
        seen_uas.add(request.headers["User-Agent"])
        assert request.headers["Range"] == "bytes=0-0"
        if str(request.url) in present:
            return httpx.Response(206, content=b"%")
        return httpx.Response(403, content=b"<Error><Code>AccessDenied</Code></Error>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    refs = HDFCAdapter().list_documents(DocType.FACTSHEET, date(2026, 6, 1),
                                        scheme_hint="HDFC Flexi Cap Fund", client=client)
    # Only month-ends up to today are probed; restrict the assertion to the months under test.
    refs = [r for r in refs if r.as_of_date <= date(2026, 8, 31)]
    assert [(r.as_of_date, r.url) for r in refs] == [
        (date(2026, 6, 30), BASE + "2026-07/HDFC%20MF%20Factsheet%20-%20June%202026.pdf"),
        (date(2026, 7, 31), BASE + "2026-08/HDFC%20MF%20Factsheet%20July%202026.pdf"),
    ]
    assert all(r.doc_type == DocType.FACTSHEET for r in refs)
    assert all(r.scheme_hint == "HDFC Flexi Cap Fund" for r in refs)
    assert seen_uas == {config.USER_AGENT}


def test_list_factsheets_works_without_scheme_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == BASE + "2026-07/HDFC%20MF%20Factsheet%20-%20June%202026.pdf":
            return httpx.Response(206, content=b"%")
        return httpx.Response(403)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    refs = HDFCAdapter().list_documents(DocType.FACTSHEET, date(2026, 6, 1), client=client)
    assert refs[0].as_of_date == date(2026, 6, 30)
    assert refs[0].scheme_hint is None
