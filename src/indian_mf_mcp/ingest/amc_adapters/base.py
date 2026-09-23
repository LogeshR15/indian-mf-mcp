"""AMC adapter protocol. Discovery (list_documents) is AMC-specific; fetch + parse are shared
(spec §7.1). Most adapters are 30-60 lines."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Protocol

import httpx

from indian_mf_mcp import config


class DocType(str, Enum):
    MONTHLY_PORTFOLIO = "monthly_portfolio"
    FORTNIGHTLY_PORTFOLIO = "fortnightly_portfolio"
    FACTSHEET = "factsheet"
    SID = "sid"
    KIM = "kim"
    ANNUAL_REPORT = "annual_report"
    ADDENDUM = "addendum"


@dataclass
class DocumentRef:
    url: str
    doc_type: DocType
    as_of_date: date | None  # best-effort guess from filename/listing; confirmed after parse
    scheme_hint: str | None = None  # free-text hint (e.g. fund code from filename) for matching


class AMCAdapter(Protocol):
    amc_id: str

    def list_documents(self, doc_type: DocType, since: date) -> list[DocumentRef]: ...
    def fetch(self, ref: DocumentRef) -> bytes: ...


def http_get(url: str, client: httpx.Client | None = None) -> bytes:
    headers = {"User-Agent": config.USER_AGENT}
    if client is not None:
        resp = client.get(url, headers=headers, follow_redirects=True, timeout=60)
    else:
        with httpx.Client() as c:
            resp = c.get(url, headers=headers, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    return resp.content


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def normalize_scheme_name(name: str) -> str:
    """Comparison key for matching a scheme hint (AMFI's name) against an AMC's own spelling of
    it. The two routinely differ only in spacing and punctuation — AMFI "Kotak Flexi Cap Fund"
    vs Kotak's index "Kotak Flexicap Fund", AMFI "UTI - Flexi Cap Fund." vs UTI's "UTI Flexi
    Cap Fund", and ICICI spelling its own file "Flexicap" one month and "Flexi Cap" the next —
    and a raw lowercase substring test missed every one of them. Lowercase, letters and digits
    only; nothing else (word order, abbreviations) is normalised away."""
    return _NON_ALNUM_RE.sub("", name.lower())
