"""HDFC Mutual Fund adapter.

HDFC was previously recorded as blocked outright. It is not — the block and the files are on
different hosts, and only *discovery* was ever affected:

  - `www.hdfcfund.com` (the disclosure listing pages, and the bare homepage) returns a flat
    edge-level `403 Access Denied` to this project's honest User-Agent. Not a CAPTCHA: a
    browser is served normally and is never challenged. That host is never used here.
  - `files.hdfcfund.com` is a separate public S3 bucket with no such rule. It serves the honest
    User-Agent a clean `200 application/vnd.openxmlformats-...` with the real workbook.

So the files were always fetchable; what was missing was a way to *name* them without the
listing page. Ground-truthed live 2026-09-13, the key is fully computable from the as-of date
and the scheme name:

    https://files.hdfcfund.com/s3fs-public/<YYYY-MM>/Monthly <SCHEME> - <D Month YYYY>.xlsx

where `<YYYY-MM>` is the month *after* the as-of month (August 2026 data is published under
`2026-09`), and `<D Month YYYY>` is the as-of date — month-end, day NOT zero-padded.

Consequences of having no listing, all handled below:
  - **Existence must be probed, one cheap ranged GET per candidate month.** `HEAD` is denied
    bucket-wide (403 even for keys that exist), so probes use `GET` with `Range: bytes=0-0`,
    which returns `206` and one byte for a hit.
  - **A missing key returns `403`, not `404`.** The bucket denies `s3:ListBucket`, so S3
    reports `AccessDenied` rather than `NoSuchKey` for anything absent. `403` here therefore
    means "no such file", and is a normal, expected, non-fatal result — not an access problem.
  - **Keys are case-sensitive and HDFC's own casing is wildly inconsistent** across schemes
    ("HDFC Nifty Metal ETF" vs "HDFC NIFTY SMALLCAP 250 ETF" vs "HDFC BSE 500 INDEX FUND"), and
    cannot be inferred without the listing. `scheme_hint` must therefore be spelled exactly as
    HDFC spells it in its own filenames — which is already what the CLI asks of every adapter.

Coverage: verified present for every month sampled back to March 2024, and intermittently
before that (September 2023 and September 2022 are absent under this convention). Probing
per month means gaps are skipped rather than guessed at.

One file per scheme per month (PPFAS/Union-style, not a combined workbook). Parses against
xlsx_portfolio.py with zero changes: exact 100% reconciliation, 95 holdings on the August 2026
Flexi Cap file.
"""
from __future__ import annotations

import calendar
import urllib.parse
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

FILES_HOST = "https://files.hdfcfund.com/s3fs-public/"

# S3 returns AccessDenied (not NoSuchKey) for absent keys because ListBucket is denied.
_ABSENT_STATUS = 403
_PRESENT_STATUSES = (200, 206)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _publication_folder(as_of: date) -> str:
    """Files are published under the month *after* the as-of month."""
    year = as_of.year + (1 if as_of.month == 12 else 0)
    month = (as_of.month % 12) + 1
    return f"{year}-{month:02d}"


def build_url(scheme_name: str, as_of: date) -> str:
    """The full S3 key for one scheme's monthly portfolio. Day is not zero-padded."""
    filename = f"Monthly {scheme_name} - {as_of.day} {as_of.strftime('%B')} {as_of.year}.xlsx"
    return FILES_HOST + _publication_folder(as_of) + "/" + urllib.parse.quote(filename)


def _month_ends_since(since: date, until: date):
    year, month = since.year, since.month
    while True:
        candidate = _month_end(year, month)
        if candidate > until:
            return
        if candidate >= since:
            yield candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


class HDFCAdapter:
    amc_id = "amc-hdfc"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO or not scheme_hint:
            # Without a scheme name there is nothing to construct: this AMC exposes no listing.
            return []

        headers = {"User-Agent": config.USER_AGENT, "Range": "bytes=0-0"}
        refs: list[DocumentRef] = []
        owns_client = client is None
        c = client or httpx.Client(timeout=30, follow_redirects=True)
        try:
            for as_of in _month_ends_since(since, date.today()):
                url = build_url(scheme_hint, as_of)
                try:
                    resp = c.get(url, headers=headers)
                except httpx.HTTPError:
                    continue  # transient; a later run re-probes this month
                if resp.status_code in _PRESENT_STATUSES:
                    refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                             as_of_date=as_of, scheme_hint=scheme_hint))
                # _ABSENT_STATUS and anything else: no file for this month, move on.
        finally:
            if owns_client:
                c.close()
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=90)
        resp.raise_for_status()
        return resp.content
