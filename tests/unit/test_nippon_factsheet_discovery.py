"""Nippon India factsheet discovery: label-keyed parsing of the downloads page (no network)."""
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType
from indian_mf_mcp.ingest.amc_adapters.nippon import (
    DOWNLOADS_PAGE,
    NipponAdapter,
    parse_factsheet_links,
)

# Trimmed verbatim-shaped rows from the live page (zero-width spaces, &#58; entities, HTML
# e-book link, drifting file names/directories, en-dash + comma label variant).
PAGE = (
    '<ul><li>​<label class="lhsLbl">​Monthly portfolio for the month of Aug 2026</label>'
    '<label class="rhsLbl"><a class="xls" href="/InvestorServices/FactsheetsDocuments/'
    'NIMF-MONTHLY-PORTFOLIO-31-Aug-26.xls">D​ownload</a></label></li>'
    '<li>​<label class="lhsLbl">Fundamentals​&#58; August ​2026​ </label>'
    '<a class="html-logo" href="/InvestorServices/FactsheetsDocuments/Fundamentals-August-2026/'
    'index.html"><img src="x.png" /></a><label class="rhsLbl">​<a class="pdf" '
    'href="/InvestorServices/FactSheetsDocuments/Nippon-FS-AUGUST-2026.pdf">Download</a></label></li>'
    '<li><label class="lhsLbl">E- Factsheet&#58; July 2026</label><label class="rhsLbl">'
    '<a class="pdf" href="/InvestorServices/FactSheetsDocuments/Nippon-FS-JULY-2026.pdf">Download</a>'
    '</label></li>'
    '<li><label class="lhsLbl">Fundamentals - January 2022</label><label class="rhsLbl">'
    '<a class="pdf" href="/InvestorServices/FactsheetsDocuments/NipponIndia-MF-Factsheet-January-2022.pdf">'
    'Download</a></label></li>'
    '<li><label class="lhsLbl">Fundamentals – January, 2016</label><label class="rhsLbl">'
    '<a class="pdf" href="/InvestorServices/FactsheetsDocuments/RMF-Factsheet-January_2016.pdf">'
    'Download</a></label></li>'
    '<li><label class="lhsLbl">Fundamentals - Feb 2021</label><label class="rhsLbl">'
    '<a class="pdf" href="/InvestorServices/Factsheets/Fundamentals-Feb-2021.pdf?v=1">Download</a>'
    '</label></li>'
    '<li><label class="lhsLbl">Changes in Riskometers in FY25</label><label class="rhsLbl">'
    '<a class="pdf" href="/InvestorServices/FactsheetsDocuments/Changes-in-Riskometers-in-FY25.pdf">'
    'Download</a></label></li></ul>'
)
B = "https://mf.nipponindiaim.com/InvestorServices/"


def test_parse_factsheet_links_keys_on_label_and_shifts_to_previous_month_end():
    assert parse_factsheet_links(PAGE) == [
        (date(2015, 12, 31), B + "FactsheetsDocuments/RMF-Factsheet-January_2016.pdf"),
        (date(2021, 1, 31), B + "Factsheets/Fundamentals-Feb-2021.pdf?v=1"),
        (date(2021, 12, 31), B + "FactsheetsDocuments/NipponIndia-MF-Factsheet-January-2022.pdf"),
        (date(2026, 6, 30), B + "FactSheetsDocuments/Nippon-FS-JULY-2026.pdf"),
        (date(2026, 7, 31), B + "FactSheetsDocuments/Nippon-FS-AUGUST-2026.pdf"),
    ]


def test_list_documents_factsheet_filters_since_and_ignores_scheme_hint():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=PAGE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        refs = NipponAdapter().list_documents(DocType.FACTSHEET, date(2026, 6, 1),
                                              scheme_hint="Nippon India Flexi Cap Fund",
                                              client=client)
    assert str(seen[0].url) == DOWNLOADS_PAGE
    assert seen[0].headers["User-Agent"] == config.USER_AGENT
    assert [(r.as_of_date, r.url.rsplit("/", 1)[1]) for r in refs] == [
        (date(2026, 6, 30), "Nippon-FS-JULY-2026.pdf"),
        (date(2026, 7, 31), "Nippon-FS-AUGUST-2026.pdf"),
    ]
    assert all(r.doc_type == DocType.FACTSHEET for r in refs)
    assert all(r.scheme_hint == "Nippon India Flexi Cap Fund" for r in refs)


def test_monthly_portfolio_listing_unaffected():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=PAGE)))
    refs = NipponAdapter().list_documents(DocType.MONTHLY_PORTFOLIO, date(2026, 1, 1), client=client)
    assert [(r.as_of_date, r.doc_type) for r in refs] == [(date(2026, 8, 31), DocType.MONTHLY_PORTFOLIO)]
