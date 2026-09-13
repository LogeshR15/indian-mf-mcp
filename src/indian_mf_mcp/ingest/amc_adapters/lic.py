"""LIC Mutual Fund adapter.

Ground-truthed live 2026-09-13: AMFI's own registry JSON at
https://www.amfiindia.com/online-center/portfolio-disclosure names the exact monthly
disclosure URL for this AMC (`amc_monthly_portfolio_disclosure`):
https://www.licmf.com/downloads/monthly-portfolio.

That page itself has no static per-file links at all (the only inline `.xlsx` href in the
server-rendered HTML is an unrelated "Dashboard" summary sheet, plus an unrelated
"Daily-Tracking-Error" file — neither is a per-scheme monthly portfolio). The real files are
served through a **plain httpx-reproducible AJAX form chain**, discovered by reading the
page's own inline `<script>` jQuery handlers (no Playwright/network capture needed — the
request shapes are spelled out directly in the page source as `$.ajax` calls):

  1. `POST /downloads/portfolio-filter-options` body `fund_category=<cat>&filter=category`
     -> `<option value='SCHEMECODE'>Scheme Name</option>` list for that category. There is
     no "all categories" option; every one of the 5 fixed categories (Equity, Hybrid,
     "ETFs & Index Funds", Debt, Solution Oriented Funds) must be queried in turn to find a
     scheme's code (discovery stops at the first category that contains a match).
  2. `POST /downloads/portfolio-filter-options` body
     `scheme_code=<code>&filter=fund_name&type=monthly_portfolio` -> `<option value='YYYY'>`
     list of years that have at least one filed monthly portfolio for that scheme.
  3. `POST /downloads/portfolio-filter-options` body
     `year=<yyyy>&filter=year&type=monthly_portfolio&scheme_code=<code>` -> `<option
     value='M'>` list of month numbers (1-12) filed for that scheme/year.
  4. `POST /downloads/portfolio-files` body
     `scheme_code=<code>&fund_name=&type=monthly_portfolio&month=<m>&year=<yyyy>` -> an HTML
     fragment with one `<a href="/assets/downloads/portfolio/monthly/<yyyy>/<m>/<file>.xlsx">`
     per filed document for that scheme/month (verified empirically that `fund_name` is
     cosmetic only — used to render the file's caption text — and can be left blank; only
     `scheme_code`/`month`/`year` actually select the file).

No session cookie or Referer is required for any of these four POSTs — verified by curling
them cold, in isolation, with the project's own honest User-Agent, and getting well-formed
`<option>`/`<a href>` fragments back every time. The href in step 4 is site-relative; joined
against `https://www.licmf.com`.

Layout: one workbook per scheme per month (PPFAS/Mirae/Union-style; **not** a combined
multi-scheme workbook — no `combined_workbook` sheet resolution needed). The single sheet is
named after the scheme code (e.g. sheet "LEEQTF" for LIC MF Flexi Cap Fund), so
`parse_portfolio_xlsx`'s default (first/only sheet) is used as-is. Header row reads "Rounded,
% to Net Assets" and values are already fractional (e.g. 0.0471 for ICICI Bank, not 4.71) —
parses cleanly against `xlsx_portfolio.py` with zero changes, same as Union/Tata. Grand total
row is a plain "GRAND TOTAL" (all caps, unlike Sundaram's "Grand Total").
"""
from __future__ import annotations

import calendar
import re
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

BASE_URL = "https://www.licmf.com"
FILTER_OPTIONS_URL = f"{BASE_URL}/downloads/portfolio-filter-options"
FILES_URL = f"{BASE_URL}/downloads/portfolio-files"

# The site exposes no "all categories" option; every fixed category must be tried in turn.
_CATEGORIES = ("Equity", "Hybrid", "ETFs & Index Funds", "Debt", "Solution Oriented Funds")

_OPTION_RE = re.compile(r"<option value='([^']*)'\s*>([^<]*)</option>")
_FILE_HREF_RE = re.compile(
    r'href="(/assets/downloads/portfolio/monthly/(\d{4})/(\d{1,2})/[^"]+\.xlsx)"',
    re.IGNORECASE,
)


def _find_scheme_code(scheme_hint: str, client: httpx.Client | None,
                       headers: dict) -> str | None:
    post = client.post if client is not None else httpx.post
    target = scheme_hint.strip().lower()
    for category in _CATEGORIES:
        resp = post(FILTER_OPTIONS_URL, data={"fund_category": category, "filter": "category"},
                    headers=headers, timeout=30)
        resp.raise_for_status()
        best = None
        for code, name in _OPTION_RE.findall(resp.text):
            if not code:
                continue
            name_l = name.strip().lower()
            if name_l == target:
                return code.strip()
            if best is None and (target in name_l or name_l in target):
                best = code.strip()
        if best is not None:
            return best
    return None


def _years_for_scheme(scheme_code: str, client: httpx.Client | None,
                       headers: dict) -> list[int]:
    post = client.post if client is not None else httpx.post
    resp = post(FILTER_OPTIONS_URL,
                data={"scheme_code": scheme_code, "filter": "fund_name",
                      "type": "monthly_portfolio"},
                headers=headers, timeout=30)
    resp.raise_for_status()
    years = []
    for value, _text in _OPTION_RE.findall(resp.text):
        if value.strip().isdigit():
            years.append(int(value.strip()))
    return years


def _months_for_year(scheme_code: str, year: int, client: httpx.Client | None,
                      headers: dict) -> list[int]:
    post = client.post if client is not None else httpx.post
    resp = post(FILTER_OPTIONS_URL,
                data={"year": str(year), "filter": "year", "type": "monthly_portfolio",
                      "scheme_code": scheme_code},
                headers=headers, timeout=30)
    resp.raise_for_status()
    months = []
    for value, _text in _OPTION_RE.findall(resp.text):
        if value.strip().isdigit():
            months.append(int(value.strip()))
    return months


class LicAdapter:
    amc_id = "amc-lic"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO or not scheme_hint:
            return []
        headers = {"User-Agent": config.USER_AGENT}

        scheme_code = _find_scheme_code(scheme_hint, client, headers)
        if scheme_code is None:
            return []

        refs: list[DocumentRef] = []
        seen = set()
        post = client.post if client is not None else httpx.post
        for year in _years_for_scheme(scheme_code, client, headers):
            if year < since.year:
                continue
            for month in _months_for_year(scheme_code, year, client, headers):
                last_day = calendar.monthrange(year, month)[1]
                as_of = date(year, month, last_day)
                if as_of < since:
                    continue
                resp = post(FILES_URL,
                            data={"scheme_code": scheme_code, "fund_name": "",
                                  "type": "monthly_portfolio", "month": str(month),
                                  "year": str(year)},
                            headers=headers, timeout=30)
                resp.raise_for_status()
                for href, url_year, url_month in _FILE_HREF_RE.findall(resp.text):
                    key = href
                    if key in seen:
                        continue
                    seen.add(key)
                    last_day2 = calendar.monthrange(int(url_year), int(url_month))[1]
                    as_of2 = date(int(url_year), int(url_month), last_day2)
                    refs.append(DocumentRef(url=f"{BASE_URL}{href}",
                                             doc_type=DocType.MONTHLY_PORTFOLIO,
                                             as_of_date=as_of2, scheme_hint=scheme_hint))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
