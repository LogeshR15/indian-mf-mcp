"""Sundaram Mutual Fund adapter.

Ground-truthed live 2026-09-13: AMFI's own registry JSON at
https://www.amfiindia.com/online-center/portfolio-disclosure names the exact monthly
disclosure URL for this AMC (`amc_monthly_portfolio_disclosure`):
https://www.sundarammutual.com/Monthly-Fortnightly-Adhoc-Portfolios — a page with no static
links; instead a category dropdown (`#Cbx_Category`) plus a `GetCategory()` JS handler drives
an ASP.NET legacy AJAX endpoint (`.ashx` "web method" file), found via a one-time Playwright
network capture and reproduced with a single plain `httpx` POST (no browser at runtime):

    POST https://www.sundarammutual.com/ajax/Modules_Disclosure_Monthly_Fortnightly_Adhoc_Portfolios,App_Web_2fjxvqiq.ashx?_method=GetCategory&_session=no
    body: Catid=Monthly

The response is a JSON-string-literal (single-quoted, with `\\'`-escaped inner quotes — not
raw HTML) containing an accordion of every year back to 2012-2013, each with one link per
month for "... Equity & Fund of Funds ..." and a separate "... Fixed Income ..." file. The
link's own anchor text states the exact month/year (e.g. "Monthly Portfolio Disclosure
Equity & Fund of Funds - Aug 2026") — no date arithmetic against a request parameter is
needed, unlike some other AMCs.

Each equity file is a *combined* workbook (one sheet per scheme, an "Index" sheet using
"ACRONYM"/"SCHEME NAME" columns — required adding "acronym" as a recognised code-column
keyword to the shared `combined_workbook.find_sheet_code`). Header wording is "% of Net
Asset" (not "% to Net Assets" or "% to AUM" as seen elsewhere), which required adding that
variant to `xlsx_portfolio.py`'s header detection. Values are already fractional (not
percentage-points), and the grand-total row is a plain "Grand Total" — no other quirks.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

CATEGORY_URL = (
    "https://www.sundarammutual.com/ajax/"
    "Modules_Disclosure_Monthly_Fortnightly_Adhoc_Portfolios,App_Web_2fjxvqiq.ashx"
)
BASE_URL = "https://www.sundarammutual.com"

_LINK_RE = re.compile(
    r"href='([^']+\.xlsx)'[^>]*>.*?mr-10[^>]*></i>([^<]+)</a>",
    re.IGNORECASE | re.DOTALL,
)
_TITLE_MONTH_RE = re.compile(r"Equity\s*&\s*Fund of Funds\s*-\s*([A-Za-z]+)\s+(\d{4})", re.IGNORECASE)


class SundaramAdapter:
    amc_id = "amc-sundaram"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"}
        post = client.post if client is not None else httpx.post
        resp = post(CATEGORY_URL, params={"_method": "GetCategory", "_session": "no"},
                     content=b"Catid=Monthly", headers=headers, timeout=30)
        resp.raise_for_status()
        text = resp.text.replace("\\'", "'")

        refs: list[DocumentRef] = []
        for path, title in _LINK_RE.findall(text):
            m = _TITLE_MONTH_RE.search(title.strip())
            if not m:
                continue  # skip "Fixed Income" files — not equity ISIN-level holdings
            month_name, year_str = m.groups()
            try:
                month_num = datetime.strptime(month_name[:3], "%b").month
            except ValueError:
                continue
            year = int(year_str)
            import calendar
            last_day = calendar.monthrange(year, month_num)[1]
            as_of = date(year, month_num, last_day)
            if as_of < since:
                continue
            url = path if path.startswith("http") else f"{BASE_URL}{path}"
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
