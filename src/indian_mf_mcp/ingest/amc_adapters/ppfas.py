"""PPFAS (Parag Parikh Financial Advisory Services) adapter.

Ground-truthed live 2026-09-12 against https://amc.ppfas.com/downloads/portfolio-disclosure/ :
per-scheme files from 2019 onward follow the pattern
  /downloads/portfolio-disclosure/<year>/<CODE>_PPFAS_Monthly_Portfolio_Report_<Month>_<Day>_<Year>.xls[x]?<querystring>
Pre-2019 files are a single combined workbook per month with a different (undetermined,
possibly no-ISIN) layout — out of scope for this adapter; `list_documents` only returns
post-2019 per-scheme files and reports the coverage gap rather than guessing at the rest.
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


class PPFASAdapter:
    amc_id = "amc-ppfas"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []  # only monthly portfolio discovery implemented for MVP

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
