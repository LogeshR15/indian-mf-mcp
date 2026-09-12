"""Tata Mutual Fund adapter.

Ground-truthed live 2026-09-12: https://www.tatamutualfund.com/schemes-related/portfolio is
a Next.js (App Router) page whose portfolio-file links are embedded server-side in the
initial HTML response itself — inside the React Server Component streaming payload
(`self.__next_f.push(...)` chunks), not a separate AJAX/API call. A plain httpx GET on the
page already returns every monthly file URL; no browser or second request is needed. (A
Playwright network capture on this page returned HTTP 403 — Tata's edge appears to
fingerprint and block headless Chromium specifically, while a plain httpx GET with a normal
User-Agent succeeds; the actual file listing came from regex-scanning the already-fetched
HTML for `https://betacms.tatamutualfund.com/system/files/.../Monthly Portfolio as on
<Day><suffix> <Month> <Year>.xlsx` URLs, which are present as escaped JSON strings in that
payload.)

Each file is a *combined* workbook covering every scheme for that month (like SBI/Motilal
Oswal — one sheet per scheme, an "Index" sheet mapping scheme names to short codes), not one
file per scheme like PPFAS/Mirae. Tata's Index sheet uses "SCHEME CODE"/"SCHEME NAME" column
headers, handled by the same shared, column-detecting `combined_workbook.find_sheet_code`.

Also note: Tata's individual scheme sheets never say "GRAND TOTAL" and instead label the
true 100% total row plain "NET ASSETS" (with sub-total rows suffixed "... TOTAL" rather than
prefixed) — both quirks required generalising `xlsx_portfolio.py` itself, not just this
adapter (see `_EXPLICIT_GRAND_TOTAL_LABELS` and the broadened `_is_aggregate_label`).
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

PORTFOLIO_PAGE_URL = "https://www.tatamutualfund.com/schemes-related/portfolio"

# Matches e.g. "Monthly%20Portfolio%20as%20on%2031st%20August%202026.xlsx" inside the
# escaped JSON payload — forward slashes may or may not be backslash-escaped depending on
# JSON nesting depth, so both are tolerated.
_LINK_RE = re.compile(
    r"https?:\\?/\\?/[^\"\\]*Monthly%20Portfolio%20as(?:%20on)?%20(\d{1,2})[a-z]{2}%20"
    r"([A-Za-z]+)%20(\d{4})[^\"\\]*\.xlsx",
    re.IGNORECASE,
)


class TataAdapter:
    amc_id = "amc-tata"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(PORTFOLIO_PAGE_URL, headers=headers, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text

        refs: list[DocumentRef] = []
        seen = set()
        for match in _LINK_RE.finditer(text):
            day, month_name, year = match.groups()
            try:
                as_of = datetime.strptime(f"{day} {month_name} {year}", "%d %B %Y").date()
            except ValueError:
                continue
            if as_of < since:
                continue
            url = match.group(0).replace("\\/", "/")
            key = (url.split("?")[0], as_of)
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
