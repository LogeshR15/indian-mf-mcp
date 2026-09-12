"""Baroda BNP Paribas Mutual Fund adapter.

Ground-truthed live 2026-09-13. AMFI's own registry page embeds each AMC's disclosure URLs
as JSON (`amc_monthly_portfolio_disclosure` field) — for this AMC that resolves directly to
`https://www.barodabnpparibasmf.in/downloads/monthly-portfolio-scheme`, a plain server-
rendered page with a real static `<a href=...xls>` link, no browser or API call needed for
the current month.

The file is served with a `.xls` extension but is real OOXML/XLSX by magic bytes (same
Nippon India quirk) — `xlsx_portfolio.py` opens raw bytes via `io.BytesIO`, so extension is
never trusted. It's a *combined* workbook (one sheet per scheme, an "Index" sheet), like
SBI/Motilal Oswal/Tata/Nippon — but this AMC's Index sheet header uses "Short Name" (not
"short code") for the code column, which required broadening `combined_workbook.find_sheet_code`'s
synonym list.

Historical months: the page has a year-dropdown that triggers a CSRF-token-gated AJAX POST
to `ajax-load-more-documents` (CodeIgniter backend). Reproducing that call returned 200 with
an empty body on a fresh session — likely needs additional session/form state not worth the
fragility to chase. This adapter therefore reliably serves only the LATEST disclosed month
(same real capability limit as the DSP adapter), documented here rather than silently
guessing at historical URLs.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

PAGE_URL = "https://www.barodabnpparibasmf.in/downloads/monthly-portfolio-scheme"

_LINK_RE = re.compile(
    r'href="([^"]+Monthly_Portfolio_(\d{1,2})-(\d{1,2})-(\d{4})_\d+\.xlsx?)"', re.IGNORECASE
)


class BarodaBNPParibasAdapter:
    amc_id = "amc-baroda-bnp-paribas"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(PAGE_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        html = resp.text

        refs: list[DocumentRef] = []
        seen = set()
        for m in _LINK_RE.finditer(html):
            url, day, month, year = m.groups()
            try:
                as_of = date(int(year), int(month), int(day))
            except ValueError:
                continue
            if as_of < since:
                continue
            key = url.split("?")[0]
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
