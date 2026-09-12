"""SBI Mutual Fund adapter.

Ground-truthed live 2026-09-12: unlike PPFAS (one file per scheme), SBI's disclosure page
(https://www.sbimf.com/en-us/portfolios) is a JavaScript SPA with no download links in the raw
HTML — the actual links come from a POST to a backing JSON/HTML endpoint,
`https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets`
(body `{"FundId":"","PSYear":"","PSMonth":"","PSFrequency":"Monthly"}`), which was found via a
one-time Playwright network-capture (not shipped at runtime — plain httpx POST reproduces it
exactly). Each returned file is a single combined workbook covering ALL ~120 SBI schemes, one
sheet per scheme plus an "Index" sheet mapping short-codes to scheme names — so this adapter
must fetch once per month and select the right sheet per scheme, rather than one file per scheme.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

PORTFOLIO_SHEETS_URL = "https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets"

_LINK_RE = re.compile(
    r'href="([^"]+all-schemes-monthly-portfolio---as-on-(\d{1,2})[a-z]{2}-([a-z]+)-(\d{4})\.xlsx[^"]*)"',
    re.IGNORECASE,
)


class SBIAdapter:
    amc_id = "amc-sbi"

    def _headers(self) -> dict:
        return {
            "User-Agent": config.USER_AGENT,
            "x-requested-with": "XMLHttpRequest",
            "content-type": "application/json;charset=UTF-8",
            "referer": "https://www.sbimf.com/portfolios",
        }

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        body = {"FundId": "", "PSYear": "", "PSMonth": "", "PSFrequency": "Monthly"}
        if client is not None:
            resp = client.post(PORTFOLIO_SHEETS_URL, json=body, headers=self._headers(), timeout=30)
        else:
            with httpx.Client() as c:
                resp = c.post(PORTFOLIO_SHEETS_URL, json=body, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        html = resp.text

        refs: list[DocumentRef] = []
        seen = set()
        for match in _LINK_RE.finditer(html):
            url, day, month_name, year = match.groups()
            try:
                as_of = datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y").date()
            except ValueError:
                continue
            if as_of < since:
                continue
            key = (url.split("?")[0], as_of)
            if key in seen:
                continue
            seen.add(key)
            # scheme_hint carries which sheet to select post-download (combined workbook,
            # not one file per scheme like PPFAS) — fetch() alone cannot know that; the
            # ingest orchestrator passes it through to the sheet-aware parser call.
            refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO, as_of_date=as_of,
                                     scheme_hint=scheme_hint))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        if client is not None:
            resp = client.get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        else:
            with httpx.Client() as c:
                resp = c.get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content

    @staticmethod
    def find_sheet_code(index_sheet_rows, scheme_name: str) -> str | None:
        """Look up a scheme's sheet short-code from the workbook's own "Index" sheet, by
        substring match on the disclosed scheme name (spec: never hard-code a mapping that
        the source itself publishes and can change)."""
        target = scheme_name.strip().lower()
        for row in index_sheet_rows:
            name = row[2] if len(row) > 2 else None
            if isinstance(name, str) and (target in name.lower() or name.lower() in target):
                return row[1]
        return None
