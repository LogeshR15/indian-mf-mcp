"""SBI factsheet discovery: parsing of the GetFactSheets HTML fragment and book selection
(combined active book vs separate passives book). No network — HTTP is mocked."""
import json
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType
from indian_mf_mcp.ingest.amc_adapters.sbi import (
    FACTSHEETS_URL,
    SBIAdapter,
    _is_passive_scheme,
    parse_factsheet_listing,
)

D = "https://www.sbimf.com/docs/default-source/scheme-factsheets/"


def _row(href: str, title: str) -> str:
    # Verbatim shape of the live fragment (including the double space in per-scheme titles).
    return (
        f'<tr>\n<td><a href="{href}" target="_blank">{title}</a></td>\n'
        '<td>  <img src="/Content/Images/halfyearly/pdf_ic.jpg" /></td>\n<td>5  MB</td>\n'
        f'<td class="text-center"><a href="{href}" class="primary-button" download="true">Download</a></td>\n</tr>'
    )


JUNE_2026 = "".join([
    _row(D + "sbi-mf-passives-(index-etf-fof)-factsheet-june-2026.pdf?sfvrsn=2bf87e20_4",
         "SBI MF Passives (Index ETF FOF) Factsheet June 2026"),
    _row(D + "sbi-flexicap-fund-factsheet-june-2026.pdf?sfvrsn=41a3623c_2",
         "SBI Flexicap Fund  Factsheet June 2026"),
    _row(D + "all-sbimf-schemes-factsheet-june-2026.pdf?sfvrsn=40d486d1_8",
         "All SBIMF Schemes Factsheet June 2026"),
])
JUNE_2020 = "".join([
    _row(D + "june-2020.pdf?sfvrsn=f0d987f0_4", "All SBIMF Schemes Factsheet June 2020"),
    _row(D + "sbi-index-solution-factsheet-june-2020.pdf?sfvrsn=18a33687_6",
         "All SBIMF Schemes Factsheet (Index) June 2020"),
    _row(D + "sbi-small-cap-fund-factsheet-04fe5419.pdf?sfvrsn=11b8b0ab_2", "SBI Small Cap Fund Factsheet"),
])
FEB_2013 = _row(D + "february-2013.pdf?sfvrsn=e0fb1f6c_4", "February 2013")
NO_RECORDS = "\n    <td>No Records Found</td>\n    <td></td>\n"


def test_parse_listing_keeps_only_combined_books_dated_by_title():
    assert parse_factsheet_listing(JUNE_2026) == [
        (D + "sbi-mf-passives-(index-etf-fof)-factsheet-june-2026.pdf?sfvrsn=2bf87e20_4", "passive",
         date(2026, 6, 30)),
        (D + "all-sbimf-schemes-factsheet-june-2026.pdf?sfvrsn=40d486d1_8", "main", date(2026, 6, 30)),
    ]
    # URL slug carries no "all-sbimf" marker in 2020; the title is what identifies the book.
    assert [(b, d) for _, b, d in parse_factsheet_listing(JUNE_2020)] == [
        ("main", date(2020, 6, 30)), ("passive", date(2020, 6, 30))]
    assert parse_factsheet_listing(FEB_2013) == [
        (D + "february-2013.pdf?sfvrsn=e0fb1f6c_4", "main", date(2013, 2, 28))]
    assert parse_factsheet_listing(NO_RECORDS) == []


def test_passive_heuristic():
    for name in ["SBI Nifty Index Fund", "SBI Nifty 50 ETF", "SBI Gold Fund", "SBI Silver ETF FoF",
                 "SBI CRISIL IBX Gilt Index - June 2036 Fund", "SBI BSE Sensex Index Fund"]:
        assert _is_passive_scheme(name), name
    for name in ["SBI Flexicap Fund", "SBI Banking & PSU Debt Fund", "SBI PSU Fund",
                 "SBI Dynamic Asset Allocation Active FOF", "SBI US Specific Equity Active FoF",
                 "SBI Multi Asset Allocation Fund"]:
        assert not _is_passive_scheme(name), name


def _client(seen: list):
    listings = {("2026", "June"): JUNE_2026}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == FACTSHEETS_URL
        assert request.headers["user-agent"] == config.USER_AGENT
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, text=listings.get((body["FSYear"], body["FSMonth"]), NO_RECORDS))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_documents_selects_book_by_scheme_hint():
    seen: list = []
    a = SBIAdapter()
    with _client(seen) as c:
        active = a.list_documents(DocType.FACTSHEET, date(2026, 5, 15), "SBI Flexicap Fund", client=c)
        passive = a.list_documents(DocType.FACTSHEET, date(2026, 5, 15), "SBI Nifty Index Fund", client=c)
        both = a.list_documents(DocType.FACTSHEET, date(2026, 5, 15), None, client=c)
        after = a.list_documents(DocType.FACTSHEET, date(2026, 7, 1), "SBI Flexicap Fund", client=c)

    assert [(r.url, r.as_of_date, r.doc_type, r.scheme_hint) for r in active] == [
        (D + "all-sbimf-schemes-factsheet-june-2026.pdf?sfvrsn=40d486d1_8", date(2026, 6, 30),
         DocType.FACTSHEET, "SBI Flexicap Fund")]
    assert [r.url for r in passive] == [
        D + "sbi-mf-passives-(index-etf-fof)-factsheet-june-2026.pdf?sfvrsn=2bf87e20_4"]
    assert len(both) == 2
    assert after == []
    # History is reached month by month starting at `since`'s month.
    assert seen[0] == {"FundId": "", "FSYear": "2026", "FSMonth": "May"}
