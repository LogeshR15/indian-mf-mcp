"""Mirae Asset Mutual Fund adapter.

Ground-truthed live 2026-09-12: https://www.miraeassetmf.co.in/downloads/portfolio is a JS
SPA with no links in raw HTML. A one-time Playwright network capture found the backing
endpoint (never a headless browser at runtime — plain httpx POST reproduces it exactly):
`POST AjaxService/GetDownloadsData` with body
`{"request":{"modulename":"portfolio_tab1","pgno":<n>,"pgsize":<n>}}`, returning a paginated
list of `{Title, URL, PublishDate}` — one static XLSX per scheme per month (PPFAS-style
layout, not a combined workbook), titled "Portfolio Details as on <date> for <Scheme Name>".
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

DOWNLOADS_API = "https://www.miraeassetmf.co.in/AjaxService/GetDownloadsData"
BASE_URL = "https://www.miraeassetmf.co.in"

_TITLE_RE = re.compile(r"as on\s+(\d{1,2})[a-z]{2}\s+([A-Za-z]+)\s+(\d{4})\s+for\s+(.+)$", re.IGNORECASE)


class MiraeAdapter:
    amc_id = "amc-mirae"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        body = {"request": {"modulename": "portfolio_tab1", "pgno": 1, "pgsize": 500}}
        post = client.post if client is not None else httpx.post
        resp = post(DOWNLOADS_API, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        rows = resp.json().get("Data", [])

        refs: list[DocumentRef] = []
        target = (scheme_hint or "").strip().lower()
        for row in rows:
            title = row.get("Title", "")
            url = row.get("URL", "")
            if not url.lower().endswith((".xlsx", ".xls")):
                continue
            m = _TITLE_RE.search(title)
            if not m:
                continue
            day, month_name, year, scheme_name = m.groups()
            if target and target not in scheme_name.lower() and scheme_name.lower() not in target:
                continue
            try:
                as_of = datetime.strptime(f"{day} {month_name} {year}", "%d %B %Y").date()
            except ValueError:
                continue
            if as_of < since:
                continue
            full_url = url if url.startswith("http") else f"{BASE_URL}{url}"
            refs.append(DocumentRef(url=full_url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                     as_of_date=as_of, scheme_hint=scheme_name.strip()))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
