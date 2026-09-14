"""Bank of India Mutual Fund adapter.

Ground-truthed live 2026-09-13: AMFI's own registry names this AMC's monthly-disclosure page
as https://www.boimf.in/investor-corner#t2 ("Monthly Portfolio" tab). The page itself is a
Sitefinity/Telerik ASP.NET site where every Investor Corner tab (`#t1`..`#t9`) is populated by
client-side JS (`NoCategoryCall()` in the site's own bundled
`assets/js/AjaxCall.js`) rather than server-rendered links or an inline array literal — so a
plain `httpx.get()` of the page HTML finds nothing. Reading that already-public JS file
(no Playwright/network-capture needed at all, since the handler source is served as a plain
static asset) reveals the backing call directly:

    POST https://www.boimf.in/AjaxService.asmx/GetDocuments
    Content-Type: application/json;charset=utf-8
    body: {"pagno": 0, "category": null, "fromDate": null, "toDate": null,
           "LibraryName": "InvestorCorner", "folderName": "MONTHLY PORTFOLIO",
           "CategoryValue": "no"}

`LibraryName` is derived client-side from whether the current page URL contains "investor"
(-> "InvestorCorner") vs "regulatory"/other; `folderName` is simply the clicked tab's own
link text, uppercased. Reproduced with one plain `httpx.post()` — no browser driven at
runtime. The response is an ASP.NET-classic double-encoded JSON envelope
(`{"d": "<json-string>"}`) whose `d` field is itself a JSON string that must be parsed a
second time to reach `{"Documents": [...], "Length": N}`. `pagno` and pagination are purely
client-side (rendered 10-per-page in the browser) — the single POST above already returns
every document in one shot (346 entries observed, back to 2012), so no paging loop is needed.

Every recent month (current back to at least Feb-2021, and intermittently earlier) publishes
one *combined* workbook per month covering every scheme — same shape as Sundaram/SBI/Tata:
an "Index" sheet with a plain "Scheme Code" / "Scheme Names" header (matches
`combined_workbook.find_sheet_code` with zero changes), and one data sheet per scheme keyed
by its short code (e.g. "YB36" for Bank of India Flexi Cap Fund). Per-scheme sheets use the
exact same header wording/column layout as PPFAS ("Name of the Instrument", "% to Net
Assets", "Market/Fair Value (Rs. in Lacs)") and a plain "GRAND TOTAL" row — parses against
`xlsx_portfolio.py` with zero changes, values already fractional.

The `GetDocuments` listing also mixes in legacy/ad-hoc entries going back to 2012: `.xls`
(legacy BIFF, pre-2021), `.xlsb`, `.pdf` liquid-fund ad-hoc disclosures, and one-off titles
with no parseable date ("PORTFOLIO - SMALL CAP FUND", "MARCH 17 - BOI AXA CORPORATE CREDIT
SPECTRUM FUND", etc. — mostly pre-2021, from when the AMC was still "BOI AXA Mutual Fund").
This adapter surfaces both `.xlsx` and `.xls` entries whose title yields a parseable
day/month-name/year — verified live: the Sep-2021 combined workbook is real legacy BIFF
(`sniff()` reports `XLS_BIFF`), resolves via the same `find_sheet_code` Index-sheet lookup
with zero changes, and parses at exact 100% reconciliation (79 holdings, Flexi Cap Fund /
`YB36`) via `parse_portfolio_xls`. `.xlsb` (a different binary container neither openpyxl nor
xlrd read) and `.pdf` entries, plus titles with no parseable date, remain genuinely
unparseable and are still silently skipped rather than guessed at.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

DOCUMENTS_ENDPOINT = "https://www.boimf.in/AjaxService.asmx/GetDocuments"

_DATE_RE = re.compile(r"(\d{1,2})\D{0,4}?([A-Za-z]{3,})\D{0,2}?(\d{4})")


class BankOfIndiaAdapter:
    amc_id = "amc-bank-of-india"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT, "Content-Type": "application/json;charset=utf-8"}
        payload = {
            "pagno": 0,
            "category": None,
            "fromDate": None,
            "toDate": None,
            "LibraryName": "InvestorCorner",
            "folderName": "MONTHLY PORTFOLIO",
            "CategoryValue": "no",
        }
        post = client.post if client is not None else httpx.post
        resp = post(DOCUMENTS_ENDPOINT, content=json.dumps(payload), headers=headers, timeout=45)
        resp.raise_for_status()
        outer = resp.json()
        inner = json.loads(outer["d"]) if outer.get("d") else {"Documents": []}

        refs: list[DocumentRef] = []
        seen = set()
        for doc in inner.get("Documents", []):
            url = (doc.get("FolderUrl") or "").strip()
            if not url:
                continue
            url_path = url.split("?")[0]
            if not url_path.lower().endswith((".xlsx", ".xls")):
                continue  # .xlsb and ad-hoc .pdf entries remain genuinely unparseable
            name = doc.get("DocName") or ""
            m = _DATE_RE.search(name)
            if not m:
                continue  # one-off titles with no parseable date (mostly pre-2021)
            day, month_name, year_str = m.groups()
            try:
                month_num = datetime.strptime(month_name[:3].title(), "%b").month
            except ValueError:
                continue
            try:
                as_of = date(int(year_str), month_num, int(day))
            except ValueError:
                continue
            if as_of < since:
                continue
            key = (url_path, as_of)
            if key in seen:
                continue
            seen.add(key)
            refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                     as_of_date=as_of, scheme_hint=scheme_hint))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
