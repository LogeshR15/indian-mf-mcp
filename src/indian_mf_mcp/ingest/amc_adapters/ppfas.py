"""PPFAS (Parag Parikh Financial Advisory Services) adapter.

Ground-truthed live 2026-09-12 against https://amc.ppfas.com/downloads/portfolio-disclosure/ :
per-scheme files from 2019 onward follow the pattern
  /downloads/portfolio-disclosure/<year>/<CODE>_PPFAS_Monthly_Portfolio_Report_<Month>_<Day>_<Year>.xls[x]?<querystring>
Pre-2019 files are a single combined workbook per month with a different (undetermined,
possibly no-ISIN) layout — out of scope for this adapter; `list_documents` only returns
post-2019 per-scheme files and reports the coverage gap rather than guessing at the rest.

Factsheets — ground-truthed live 2026-09-23 against https://amc.ppfas.com/downloads/factsheet/ :
the page is server-rendered and links every monthly factsheet PDF from June 2013 to date (one
per month, complete: 12/year from 2014), all under /downloads/factsheet/<year>/[<month>/].
ONE combined PDF per month covers every scheme (each scheme gets its own run of pages, titled
with the scheme name at the top), so the same ref is returned whatever the scheme_hint and the
caller scopes pages to the scheme. The filename convention drifted nine times
("pltvf-factsheet-<month>-<year>", "ppfas-mf-factsheet-<month>-<year>",
"ppfas-mf-factsheet-for-<Month>-<year>", "PPFCF-factsheet-…", "facsheet_<Month>_<year>_New",
"…-<month>web-<year>", "…-new"), so discovery keys on "a PDF under /downloads/factsheet/ whose
filename names a month and a year" rather than on any one pattern. A query string (?DDMMYYYY
cache-buster) is appended to most links and kept on the URL; de-duplication is by month.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef, http_get

REGISTRY_URL = "https://amc.ppfas.com/downloads/portfolio-disclosure/"

# Scheme fund-codes as used in PPFAS's own filenames -> canonical scheme name fragment,
# used as scheme_hint for matching against our scheme table (base_scheme_name).
FUND_CODES = {
    "PPFCF": "Parag Parikh Flexi Cap Fund",
    "PPLF": "Parag Parikh Liquid Fund",
    "PPTSF": "Parag Parikh Tax Saver Fund",
    "PPCHF": "Parag Parikh Conservative Hybrid Fund",
    "PPAF": "Parag Parikh Arbitrage Fund",
    "PPDAAF": "Parag Parikh Dynamic Asset Allocation Fund",
    "PPLCF": "Parag Parikh Large Cap Fund",
}

_LINK_RE = re.compile(
    r'href="([^"]+/(\d{4})/([A-Z]+)_PPFAS_Monthly_Portfolio_Report_'
    r'([A-Za-z]+)_(\d{1,2})_(\d{4})\.(xlsx|xls))(?:\?[^"]*)?"',
    re.IGNORECASE,
)


FACTSHEET_URL = "https://amc.ppfas.com/downloads/factsheet/"

_FACTSHEET_LINK_RE = re.compile(r'href=["\']([^"\']*/downloads/factsheet/[^"\']+?\.pdf)(\?[^"\']*)?["\']',
                                re.IGNORECASE)
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_FILENAME_MONTH_YEAR_RE = re.compile(rf"({'|'.join(_MONTHS)})\D{{0,6}}?(20\d\d)", re.IGNORECASE)


def _month_end(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def parse_factsheet_links(html: str, since: date) -> list[tuple[str, date]]:
    """(url, month-end as-of date) for every monthly factsheet PDF on the listing page dated on
    or after `since`, one per month, oldest first. When a month has more than one file (a
    re-issued "…-new" copy), the re-issue wins."""
    by_month: dict[date, str] = {}
    for m in _FACTSHEET_LINK_RE.finditer(html):
        path, query = m.group(1), m.group(2) or ""
        fname = path.rsplit("/", 1)[-1]
        mm = _FILENAME_MONTH_YEAR_RE.search(fname)
        if mm is None:
            continue  # e.g. calculation-methodology.pdf
        as_of = _month_end(int(mm.group(2)), _MONTHS.index(mm.group(1).lower()) + 1)
        if as_of < since:
            continue
        url = (path if path.startswith("http") else f"https://amc.ppfas.com{path}") + query
        if as_of not in by_month or "new" in fname.lower():
            by_month[as_of] = url
    return sorted(((u, d) for d, u in by_month.items()), key=lambda ud: ud[1])


class PPFASAdapter:
    amc_id = "amc-ppfas"
    factsheet_scope = "combined"  # one PDF per month for every scheme (see module docstring)

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type == DocType.FACTSHEET:
            html = http_get(FACTSHEET_URL, client=client).decode("utf-8", errors="replace")
            return [DocumentRef(url=url, doc_type=DocType.FACTSHEET, as_of_date=as_of,
                                scheme_hint=scheme_hint)
                    for url, as_of in parse_factsheet_links(html, since)]
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []

        raw = http_get(REGISTRY_URL, client=client)
        html = raw.decode("utf-8", errors="replace")
        refs: list[DocumentRef] = []
        for match in _LINK_RE.finditer(html):
            path, year, code, month_name, day, year2, ext = match.groups()
            code = code.upper()
            if scheme_hint and FUND_CODES.get(code) != scheme_hint:
                continue
            try:
                as_of = datetime.strptime(f"{month_name} {day} {year2}", "%B %d %Y").date()
            except ValueError:
                continue
            if as_of < since:
                continue
            url = path if path.startswith("http") else f"https://amc.ppfas.com{path}"
            refs.append(DocumentRef(
                url=url, doc_type=DocType.MONTHLY_PORTFOLIO, as_of_date=as_of,
                scheme_hint=FUND_CODES.get(code, code),
            ))
        # de-dupe (same file can appear more than once on the page)
        seen = set()
        deduped = []
        for r in refs:
            key = (r.url.split("?")[0], r.as_of_date, r.scheme_hint)
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        deduped.sort(key=lambda r: r.as_of_date)
        return deduped

    def fetch(self, ref, client: httpx.Client | None = None) -> bytes:
        return http_get(ref.url, client=client)
