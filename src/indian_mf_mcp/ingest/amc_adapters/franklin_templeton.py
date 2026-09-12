"""Franklin Templeton (India) Mutual Fund adapter.

Ground-truthed live 2026-09-13. The AMFI registry links to
https://www.franklintempletonindia.com/investor/reports?firstFilter-6, which redirects to an
Angular SPA (`/reports`) with no static file links in raw HTML. A one-time capture found the
chain (never a headless browser at runtime — plain httpx reproduces every step):
  1. `GET api/literature/v1/responseLitJson?type=report` — returns the FULL literature catalog
     (~3MB JSON) as `{"FirstDropDown": [{"id": <category>, "dataRecords": {"linkdata": [...]}}]}`.
     Filter items where `id == "MONTHLY-PORTFOLIO-DSCLR"`; each has `documentId`,
     `dctermsTitle` (e.g. "ISIN as on 31 August 2026"), and `literatureHref` (an SPA route,
     NOT a direct file URL).
  2. The actual binary download is `literatureHref` with `"download"` prefixed onto the path
     (found via the main JS bundle: `L.indexOf("http")>-1?L:"download"+x` in the file-open
     handler) — i.e. `https://www.franklintempletonindia.com/download<literatureHref>`.

Each monthly file is a *combined* workbook covering every scheme (like SBI/Motilal
Oswal/Tata), but with NO separate "Index" lookup sheet at all — the sheet name itself IS the
scheme's short code (e.g. "FBIF", "FILCF"), and the scheme's full name is only in that
sheet's own row 1. Uses `combined_workbook.find_sheet_by_title`, not `find_sheet_code`.

Also: this AMC puts the ISIN column *before* the name column (reverse of every other AMC
handled so far) — section/total/summary rows then put their label text in that leading ISIN
column position instead of the name column, which required generalizing the label-extraction
fallback in `xlsx_portfolio.py` itself (see the `used_isin_as_label` handling there).
"""
from __future__ import annotations

from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

LITERATURE_API = "https://www.franklintempletonindia.com/api/literature/v1/responseLitJson"
BASE_URL = "https://www.franklintempletonindia.com"
CATEGORY_ID = "MONTHLY-PORTFOLIO-DSCLR"


class FranklinTempletonAdapter:
    amc_id = "amc-franklin-templeton"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(LITERATURE_API, params={"type": "report"}, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        refs: list[DocumentRef] = []
        for section in data.get("FirstDropDown", []):
            if section.get("id") != CATEGORY_ID:
                continue
            for item in section.get("dataRecords", {}).get("linkdata", []):
                ref_date_str = item.get("frkReferenceDate")
                href = item.get("literatureHref")
                if not ref_date_str or not href or not href.lower().endswith((".xlsx", ".xls")):
                    continue
                try:
                    as_of = datetime.strptime(ref_date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if as_of < since:
                    continue
                url = f"{BASE_URL}/download{href}"
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
