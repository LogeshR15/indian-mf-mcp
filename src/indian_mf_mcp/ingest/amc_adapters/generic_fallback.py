"""Generic fallback adapter: scrapes any AMC disclosure page for links matching (xls|xlsx|pdf)
with a date-like filename. Works for perhaps half the long tail (spec §7.1); degrades honestly
(returns fewer/no documents) for the rest rather than guessing at a bespoke page structure.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

import httpx

from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef, http_get

_LINK_RE = re.compile(r'href="([^"]+\.(?:xlsx|xls|pdf))(?:\?[^"]*)?"', re.IGNORECASE)
_DATE_HINTS = [
    re.compile(r"(20\d{2})[-_]?([01]?\d)[-_]?([0-3]?\d)"),
    re.compile(r"([A-Za-z]+)[-_](\d{1,2})[-_](20\d{2})"),
]


class GenericFallbackAdapter:
    def __init__(self, amc_id: str, registry_url: str):
        self.amc_id = amc_id
        self.registry_url = registry_url

    def list_documents(self, doc_type: DocType, since: date,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        raw = http_get(self.registry_url, client=client)
        html = raw.decode("utf-8", errors="replace")
        refs: list[DocumentRef] = []
        for m in _LINK_RE.finditer(html):
            path = m.group(1)
            url = urljoin(self.registry_url, path)
            refs.append(DocumentRef(url=url, doc_type=doc_type, as_of_date=None))
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        return http_get(ref.url, client=client)
