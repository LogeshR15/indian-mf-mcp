"""UTI Mutual Fund adapter.

Ground-truthed live 2026-09-12: https://www.utimf.com/downloads/consolidate-all-portfolio-disclosure
is a JS-rendered SPA with no download links in raw HTML. A one-time Playwright network capture
found the real endpoints (never a headless browser at runtime — plain httpx reproduces both):
  - `GET api/get_investor_scheme_fund` — every scheme's `field_dofa_schcode` (the code this
    adapter needs) plus, as a bonus, current fund manager names for free.
  - `GET api/get-scheme-portfolio-disclosure?dofa_scheme_code=<code>&year=<YYYY>&month=<FullMonthName>`
    — returns the actual XLSX URL. Note `month` must be the full English month name (e.g.
    "August"), not a number — a zero-padded or bare numeric month silently returns no rows.
"""
from __future__ import annotations

from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

SCHEME_LIST_URL = "https://www.utimf.com/api/get_investor_scheme_fund"
PORTFOLIO_URL = "https://www.utimf.com/api/get-scheme-portfolio-disclosure"

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December"]


class UTIAdapter:
    amc_id = "amc-uti"

    def _find_dofa_code(self, scheme_hint: str, client: httpx.Client | None = None) -> str | None:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(SCHEME_LIST_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        target = scheme_hint.strip().lower()
        for item in data:
            name = (item.get("field_fund_name") or "").lower()
            if target in name or name in target:
                return item.get("field_dofa_schcode")
        return None

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO or not scheme_hint:
            return []
        dofa_code = self._find_dofa_code(scheme_hint, client=client)
        if dofa_code is None:
            return []

        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        refs: list[DocumentRef] = []
        today = date.today()
        cur = date(today.year, today.month, 1)
        while cur >= date(since.year, since.month, 1):
            month_name = _MONTH_NAMES[cur.month - 1]
            resp = get(PORTFOLIO_URL, params={
                "dofa_scheme_code": dofa_code, "year": str(cur.year), "month": month_name,
            }, headers=headers, timeout=30)
            resp.raise_for_status()
            rows = resp.json().get("rows", [])
            for row in rows:
                url = row.get("doc") or row.get("url")
                if url and url.lower().endswith((".xlsx", ".xls")):
                    # month-end date approximation; exact as-of is re-derived from the parsed
                    # file's own "AS OF" text where available.
                    if cur.month == 12:
                        next_month = date(cur.year + 1, 1, 1)
                    else:
                        next_month = date(cur.year, cur.month + 1, 1)
                    as_of = next_month - __import__("datetime").timedelta(days=1)
                    refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                             as_of_date=as_of, scheme_hint=scheme_hint))
            if cur.month == 1:
                cur = date(cur.year - 1, 12, 1)
            else:
                cur = date(cur.year, cur.month - 1, 1)
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
