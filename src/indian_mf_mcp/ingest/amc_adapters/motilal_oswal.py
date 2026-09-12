"""Motilal Oswal Mutual Fund adapter.

Ground-truthed live 2026-09-12: the disclosure page
(https://www.motilaloswalmf.com/downloads/scheme-portfolio-details) is an AEM-backed page
whose actual document listing comes from a JSON search API — found via a one-time Playwright
network capture (never a headless browser at runtime; plain httpx reproduces it exactly):
`GET .../api/search-documents.json?year=&category=month end portfolio&month=&type=mf`.
Each result is a *combined* workbook covering every scheme for that month (like SBI — one
sheet per scheme, an "Index" sheet mapping scheme names to sheet codes), not one file per
scheme like PPFAS/Mirae. Filenames are messy/inconsistent (e.g. "Copy of Scheme Portfolio
Details Aug 2026.xlsx") — the `publishDate` field, not the filename, is used for `as_of_date`.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from urllib.parse import quote

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

SEARCH_API = "https://www.motilaloswalmf.com/content/aem-cloud-dept-backend-motilal-oswal/api/search-documents.json"
BASE_URL = "https://www.motilaloswalmf.com"

# "scheme portfolio details august 2026" -> (August, 2026). The API's own `publishDate` is
# when the file was *uploaded* (often the following month), not the portfolio's as-of date —
# using it directly would misdate every snapshot by ~10 days into the wrong month.
_TITLE_MONTH_RE = re.compile(r"([a-z]+)\s+(\d{4})$")


class MotilalOswalAdapter:
    amc_id = "amc-motilal-oswal"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(SEARCH_API, params={"year": "", "category": "month end portfolio",
                                         "month": "", "type": "mf"}, headers=headers, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])

        refs: list[DocumentRef] = []
        seen = set()
        for r in results:
            title = (r.get("title") or "").lower()
            path = r.get("path") or ""
            if "fortnightly" in title or not path.lower().endswith((".xlsx", ".xls")):
                continue  # equity/debt monthly "Scheme Portfolio Details" only, not fortnightly debt
            if "scheme portfolio details" not in title:
                continue
            m = _TITLE_MONTH_RE.search(title.strip())
            if not m:
                continue
            month_name, year_str = m.groups()
            try:
                month_num = datetime.strptime(month_name, "%B").month
            except ValueError:
                continue
            year = int(year_str)
            last_day = calendar.monthrange(year, month_num)[1]
            as_of = date(year, month_num, last_day)
            if as_of < since:
                continue
            url = f"{BASE_URL}{quote(path)}"
            key = (path, as_of)
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
