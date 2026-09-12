"""Nippon India Mutual Fund (formerly Reliance Mutual Fund) adapter.

Ground-truthed live 2026-09-13: the disclosure page is SharePoint-backed
(https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-
disclosures), but — unlike most SPAs seen so far — its document links are rendered
server-side into the initial HTML itself. A plain `httpx.get()` on that page already
returns every monthly/fortnightly portfolio file URL as a relative `/InvestorServices/
FactsheetsDocuments/NIMF-MONTHLY-PORTFOLIO-<D>-<Mon>-<YY>.xls` link; no Playwright/API call
needed for discovery.

The downloaded file has a `.xls` extension but is real OOXML (ZIP/XLSX) content by magic
bytes — confirmed AMCs lie about extension constantly (spec §7). `openpyxl.load_workbook`
must be called on an in-memory `io.BytesIO(raw)`, never a bare file path, or openpyxl's own
extension-based gate incorrectly rejects it as "old .xls format" even though the content is
valid XLSX (this project's parser already does this correctly via BytesIO).

Each file is a *combined* workbook covering every scheme for that month (like SBI/Motilal
Oswal/Tata — one sheet per scheme), with an "Index" sheet mapping scheme names to short
codes — but Nippon India's Index sheet has **no header row at all**, just raw (code, name)
pairs starting at row 0 (e.g. "GF" -> "Nippon India Growth Mid Cap Fund..."). Handled by the
headerless fallback added to `combined_workbook.find_sheet_code`. Also notable: this AMC's
per-scheme sheets put ISIN *before* the instrument name in the header row (every other AMC
seen so far does it the other way around) — the column-detecting parser handles this with
no changes since it looks up each column independently by keyword, not by relative position.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

DOWNLOADS_PAGE = "https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures"
BASE_URL = "https://mf.nipponindiaim.com"

_LINK_RE = re.compile(
    r'href="(/InvestorServices/FactsheetsDocuments/NIMF-MONTHLY-PORTFOLIO-(\d{1,2})-([A-Za-z]+)-(\d{2})\.xls)"',
    re.IGNORECASE,
)


class NipponAdapter:
    amc_id = "amc-nippon-india"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(DOWNLOADS_PAGE, headers=headers, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        refs: list[DocumentRef] = []
        seen = set()
        for match in _LINK_RE.finditer(html):
            path, day, month_name, year2 = match.groups()
            try:
                as_of = datetime.strptime(f"{day} {month_name} {year2}", "%d %b %y").date()
            except ValueError:
                try:
                    as_of = datetime.strptime(f"{day} {month_name} {year2}", "%d %B %y").date()
                except ValueError:
                    continue
            if as_of < since:
                continue
            key = (path, as_of)
            if key in seen:
                continue
            seen.add(key)
            refs.append(DocumentRef(url=f"{BASE_URL}{path}", doc_type=DocType.MONTHLY_PORTFOLIO,
                                     as_of_date=as_of, scheme_hint=scheme_hint))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
