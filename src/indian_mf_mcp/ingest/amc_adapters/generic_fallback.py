"""Generic fallback adapter: scrapes any AMC disclosure page for links matching (xls|xlsx|pdf)
with a date-like filename. Works for perhaps half the long tail (spec §7.1); degrades honestly
(returns fewer/no documents) for the rest rather than guessing at a bespoke page structure.

Not currently wired into registry.py. Unlike every other adapter here — each ground-truthed
live against one specific, verified AMC disclosure URL — this one is a generic factory that
needs an `(amc_id, registry_url)` per unadapted AMC to do anything, and there is no automated
AMC -> disclosure-URL source yet (spec's "AMFI disclosure-registry scrape -> adapter bootstrap"
step, config.AMFI_PORTFOLIO_REGISTRY_URL, is defined but not yet consumed anywhere). Wiring
this in before that exists would mean guessing at unverified per-AMC URLs, which is exactly
what this project's adapters are built to avoid. Once the AMFI registry scrape lands, this
becomes the natural fallback for whatever AMCs it doesn't already have a bespoke adapter for.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urljoin

import httpx

from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef, http_get

_LINK_RE = re.compile(r'href="([^"]+\.(?:xlsx|xls|pdf))(?:\?[^"]*)?"', re.IGNORECASE)
_DATE_HINTS = [
    re.compile(r"(20\d{2})[-_]?([01]?\d)[-_]?([0-3]?\d)"),
    re.compile(r"([A-Za-z]+)[-_](\d{1,2})[-_](20\d{2})"),
]
_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _date_hint_from_filename(path: str) -> date | None:
    """Best-effort date from a URL's filename. Never raises; returns None on any mismatch."""
    fname = path.rsplit("/", 1)[-1]
    m = _DATE_HINTS[0].search(fname)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = _DATE_HINTS[1].search(fname)
    if m:
        month = _MONTH_ABBR.get(m.group(1).lower()[:3])
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                pass
    return None


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
            refs.append(DocumentRef(url=url, doc_type=doc_type,
                                     as_of_date=_date_hint_from_filename(path)))
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        return http_get(ref.url, client=client)
