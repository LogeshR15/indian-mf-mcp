"""Kotak factsheet discovery: candidate-URL generation and probe-based listing against the
www.kotakmf.com/factsheet/ S3 proxy. No network — HTTP is mocked."""
import urllib.parse
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef
from indian_mf_mcp.ingest.amc_adapters.kotak import (
    FACTSHEET_HOST,
    KotakAdapter,
    factsheet_candidate_urls,
)


def _url(folder: str, filename: str) -> str:
    return FACTSHEET_HOST + urllib.parse.quote(folder) + "/" + urllib.parse.quote(filename)


def test_candidates_cover_live_spellings_and_filename_offset():
    july = factsheet_candidate_urls(date(2026, 7, 31))
    assert july[0] == _url("July_2026", "Kotak MF Factsheet July 2026.pdf")
    # July 2026 data lives in a file named for its publication month.
    assert _url("July_2026", "Kotak MF Factsheet August 2026.pdf") in july
    assert _url("feb_2026", "Kotak MF Factsheet February 2026.pdf") in \
        factsheet_candidate_urls(date(2026, 2, 28))
    assert _url("March2026", "Kotak MF Factsheet March 2026.pdf") in \
        factsheet_candidate_urls(date(2026, 3, 31))
    assert _url("december-2024", "Kotak MF Factsheet December 2024.pdf") in \
        factsheet_candidate_urls(date(2024, 12, 31))
    # December rolls the next-month filename into the next year.
    assert _url("December-2025", "Kotak MF Factsheet January 2026.pdf") in \
        factsheet_candidate_urls(date(2025, 12, 31))
    assert len(july) == len(set(july))
    may = factsheet_candidate_urls(date(2026, 5, 31))  # "May" == its abbreviation: no dupes
    assert len(may) == len(set(may))


LIVE = {
    _url("June_2026", "Kotak MF Factsheet June 2026.pdf"),
    _url("July_2026", "Kotak MF Factsheet August 2026.pdf"),
    _url("August_2026", "Kotak MF Factsheet August 2026.pdf"),
}


def _transport(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["User-Agent"] == config.USER_AGENT
        assert request.method == "GET"  # HEAD answers 200 for any path on this host
        url = str(request.url)
        if url in LIVE:
            return httpx.Response(200, content=b"%PDF-1.4\n...",
                                  headers={"content-type": "application/pdf"})
        if "May" in url or "may" in url:  # Radware challenge must never count as a hit
            return httpx.Response(302, headers={"location": "https://validate.perfdrive.com/"})
        return httpx.Response(404, content=b"<Error><Code>NoSuchKey</Code></Error>",
                              headers={"content-type": "application/xml"})
    return httpx.MockTransport(handler)


def test_list_factsheets_probes_each_month_and_ignores_scheme_hint(monkeypatch):
    seen: list = []
    client = httpx.Client(transport=_transport(seen))

    class _Today(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 23)

    import indian_mf_mcp.ingest.amc_adapters.kotak as kotak
    monkeypatch.setattr(kotak, "date", _Today)

    refs = KotakAdapter().list_documents(DocType.FACTSHEET, date(2026, 5, 1),
                                         scheme_hint="Kotak Flexicap Fund", client=client)
    assert [(r.url, r.as_of_date) for r in refs] == [
        (_url("June_2026", "Kotak MF Factsheet June 2026.pdf"), date(2026, 6, 30)),
        (_url("July_2026", "Kotak MF Factsheet August 2026.pdf"), date(2026, 7, 31)),
        (_url("August_2026", "Kotak MF Factsheet August 2026.pdf"), date(2026, 8, 31)),
    ]
    assert all(r.doc_type == DocType.FACTSHEET and r.scheme_hint == "Kotak Flexicap Fund"
               for r in refs)
    assert not any("2026/" in r.url.path and "September" in r.url.path.split("/")[2]
                   for r in seen)  # Sep 2026 month-end is after "today": never probed

    none_hint = KotakAdapter().list_documents(DocType.FACTSHEET, date(2026, 8, 1),
                                              client=client)
    assert [r.url for r in none_hint] == [refs[-1].url] and none_hint[0].scheme_hint is None


def test_fetch_factsheet_returns_raw_pdf_bytes():
    seen: list = []
    client = httpx.Client(transport=_transport(seen))
    url = _url("August_2026", "Kotak MF Factsheet August 2026.pdf")
    ref = DocumentRef(url=url, doc_type=DocType.FACTSHEET, as_of_date=date(2026, 8, 31),
                      scheme_hint="Kotak Flexicap Fund")
    assert KotakAdapter().fetch(ref, client=client) == b"%PDF-1.4\n..."
