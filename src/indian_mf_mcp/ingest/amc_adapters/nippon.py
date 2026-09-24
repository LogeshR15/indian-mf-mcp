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

MONTHLY FACTSHEET discovery (ground-truthed live 2026-09-23)
------------------------------------------------------------
The factsheets are listed on the *same* server-rendered downloads page (plain httpx GET with
the honest `config.USER_AGENT`, 200, ~390 KB HTML) — no Playwright, no API. Nippon calls its
factsheet "Fundamentals" (historically) or "E- Factsheet" (most 2024-26 months). Each month is
one `<li>` whose left label reads e.g. `Fundamentals: August 2026`, `E- Factsheet: July 2026`,
`Fundamentals - Feb 2021` or `Fundamentals – January, 2016`, followed by a single
`<a class="pdf" href="...pdf">Download</a>` (some recent entries also carry an HTML e-book
link to `.../Fundamentals-<Month>-<YYYY>/index.html`, which we ignore — it isn't a PDF).

It is ONE combined factsheet PDF for all schemes per month (the Aug-2026 edition is ~27 MB),
so `scheme_hint` does not select a file — every hint gets the same URL, and the caller scopes
pages to the scheme later.

**The file names are NOT computable** — they drift wildly across eras and even month to month:
`FactSheetsDocuments/Nippon-FS-AUGUST-2026.pdf`, `FactSheetsDocuments/Nippon-FS-APR-2026.pdf`,
`FactSheets/Nippon-FS-Mar-2026.pdf`, `FactSheets/NipponIndia-Factsheet-Jun2024.pdf`,
`FactSheets/NippponIndia-Factsheet-January-2024.pdf` (sic, triple p),
`FactSheets/NipponIndia-MF-Factsheet-February-2022.pdf?v=1`,
`Factsheets/Fundamentals-Feb-2021.pdf`, `FactsheetsDocuments/RMF-Factsheet_December-2015.pdf`,
`Addenda/December-2014.pdf`, `FactsheetsDocuments/RMF%20Factsheet%20October%202013%20Draft.pdf`,
`FactsheetsDocuments/RelianceFactsheetOctober2012.pdf`. The directory casing varies too
(`FactSheets` / `Factsheets` / `FactSheetsDocuments` / `FactsheetsDocuments`); the SharePoint
host is case-insensitive and serves all of them. So discovery keys off the **label text** (month
+ year), never the file name, and the href is used verbatim. Labels contain zero-width spaces
(U+200B) and HTML entities (`&#58;` for ':') that must be stripped before matching.

**Label month = PUBLICATION month, not data month.** The edition labelled "Fundamentals:
August 2026" (Nippon-FS-AUGUST-2026.pdf) states "Details as on July 31, 2026" / "FUND MANAGER'S
/ EXPERIENCE AS ON JULY 31, 2026" throughout (~500 occurrences, no Aug-31 date at all). This
held in every era spot-checked by counting "as on <date>" strings in the PDF text: Jul-2026 ->
June 30 2026, Mar-2026 -> Feb 28 2026, Jul-2025 -> Jun 30 2025, Apr-2025 -> Mar 31 2025,
Jun-2024 -> May 31 2024, Feb-2022 -> Jan 31 2022, Feb-2021 -> Jan 31 2021, Jun-2019 -> May 31
2019, Oct-2015 -> 30th Sep 2015, Dec-2014 -> 30th Nov 2014, Oct-2012 -> 30th Sep 2012. So
`as_of_date` is the last calendar day of the month **before** the labelled month (the
month-end the factsheet actually describes). Consequence: the factsheet describing month M
appears on the page only once edition M+1 is published (e.g. on 2026-09-23 the latest
available describes 2026-07-31; the Aug-31 data edition, "September 2026", was not yet up).

History reach: every edition from October 2012 to the latest (label Aug 2026 as
of 2026-09-23) is linked from the page — 167 PDFs, no gaps, i.e. data months Sep-2012 ..
Jul-2026; spot-checked Oct-2012, Oct-2015 and Aug-2026,
all 200 `application/pdf`. Pre-2019 files are "Reliance Mutual Fund" branded (the AMC's former
name). Each label appeared exactly once (no duplicate months) at discovery time.
"""
from __future__ import annotations

import calendar
import html as html_lib
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

# One <li> per document row; factsheet rows have a lhsLbl label like "Fundamentals: August 2026"
# or "E- Factsheet: July 2026" and one PDF href.
_LI_SPLIT_RE = re.compile(r"<li[\s>]", re.IGNORECASE)
_LABEL_RE = re.compile(r'<label[^>]*class="lhsLbl"[^>]*>(.*?)</label>', re.IGNORECASE | re.DOTALL)
_PDF_HREF_RE = re.compile(r'href="([^"]+\.pdf(?:\?[^"]*)?)"', re.IGNORECASE)
_FACTSHEET_LABEL_RE = re.compile(
    r"^\s*(?:Fundamentals?|E-?\s*Fact\s*sheet)\s*[-:\u2013\u2014]?\s*([A-Za-z]+)\s*,?\s*(\d{4})\s*$",
    re.IGNORECASE,
)
_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTHS["sept"] = 9


def _clean_label(raw: str) -> str:
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", raw))
    text = text.replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_factsheet_links(page_html: str) -> list[tuple[date, str]]:
    """Extract (data month-end date, absolute PDF URL) for every monthly factsheet row on the
    downloads page — the date is the end of the month BEFORE the labelled edition month. Pure function (no I/O); keyed on the row label, never the file name."""
    out: dict[date, str] = {}
    for chunk in _LI_SPLIT_RE.split(page_html):
        label_m = _LABEL_RE.search(chunk)
        if not label_m:
            continue
        m = _FACTSHEET_LABEL_RE.match(_clean_label(label_m.group(1)))
        if not m:
            continue
        month = _MONTHS.get(m.group(1).lower())
        if month is None:
            continue
        href_m = _PDF_HREF_RE.search(chunk)
        if not href_m:
            continue
        # Label names the publication month; contents are as of the previous month-end.
        year = int(m.group(2))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        as_of = date(year, month, calendar.monthrange(year, month)[1])
        href = html_lib.unescape(href_m.group(1))
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        out.setdefault(as_of, url)  # page lists newest first; keep first occurrence
    return sorted(out.items())


class NipponAdapter:
    amc_id = "amc-nippon-india"
    # Factsheets: one PDF per month for every scheme.
    factsheet_scope = "combined"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type == DocType.FACTSHEET:
            return self._list_factsheets(since, scheme_hint, client)
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

    def _list_factsheets(self, since: date, scheme_hint: str | None,
                         client: httpx.Client | None) -> list[DocumentRef]:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(DOWNLOADS_PAGE, headers=headers, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        # One combined PDF per month for all schemes: scheme_hint does not pick the file.
        return [DocumentRef(url=url, doc_type=DocType.FACTSHEET, as_of_date=as_of,
                            scheme_hint=scheme_hint)
                for as_of, url in parse_factsheet_links(resp.text) if as_of >= since]

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        timeout = 180 if ref.doc_type == DocType.FACTSHEET else 60  # factsheets are ~27 MB
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=timeout)
        resp.raise_for_status()
        return resp.content
