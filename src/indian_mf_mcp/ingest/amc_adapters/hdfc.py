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

Monthly factsheet (DocType.FACTSHEET)
-------------------------------------
HDFC publishes ONE combined factsheet PDF per month covering every scheme (~100+ pages), so
`list_documents(DocType.FACTSHEET, ...)` returns the same URL whatever `scheme_hint` is (and
works with `scheme_hint=None`); scoping to a scheme's pages is the caller's job. The factsheet
listing page (`www.hdfcfund.com/investor-services/factsheets`) is behind the same edge 403 as
the rest of `www`, so it is not used. Ground-truthed live 2026-09-23 by probing the same
`files.hdfcfund.com` bucket with the honest User-Agent (the "HDFC MF Factsheet - <Month YYYY>"
name was first confirmed as a guess, then corroborated by public search results pointing at
this bucket):

    https://files.hdfcfund.com/s3fs-public/<YYYY-MM>/HDFC MF Factsheet - <Month YYYY>.pdf

where `<Month YYYY>` is the as-of month (full month name, no day) and `<YYYY-MM>` is, as for
portfolios, the month *after* it (the August 2026 factsheet lives under `2026-09`). The same
S3 rules apply: no listing, `HEAD` denied, absent key -> 403. Existence is probed with a ranged
GET, one month at a time, stopping at the first hit per month.

Quirks, all found by a month-by-month sweep of 2019-01..2026-08:
  - **The filename is not stable.** Most months use "HDFC MF Factsheet - July 2026.pdf", but
    many use the dash-less "HDFC MF Factsheet August 2024.pdf" (most of 2024-01..2024-10, also
    2023-02/04/05/11, 2021-12, 2019-10), and July 2025 exists only as a re-upload named
    "HDFC MF Factsheet - July 2025 (1).pdf" (Drupal's duplicate-name suffix). All three are
    tried, in `_FACTSHEET_NAME_PATTERNS` order.
  - **Occasional re-uploads land a month later.** A few months (e.g. May/June 2026, August
    2025, November 2022, December 2021) are *also* present under the as-of+2 folder. As-of+1
    is tried first; as-of+2 is the fallback. The as-of-month folder itself never produced a
    hit that +1 didn't, so it is not probed.
  - Worst case is 6 ranged GETs for a month that doesn't exist (e.g. the current month before
    publication, ~the 10th of the following month).

History: with those variants, every month from 2019-02 to 2026-08 resolves EXCEPT December
2023, March 2020 and January 2019, which are absent under every variant tried (dash/no-dash,
`_0`/`_1`/`(1)` suffixes, casing, en-dash, abbreviated/upper-case month, as-of/+1/+2
folders). Those gaps are skipped, never guessed at.
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


# Monthly factsheet: one combined PDF per month for all schemes. Tried in this order; see the
# module docstring for which months use which spelling.
_FACTSHEET_NAME_PATTERNS = (
    "HDFC MF Factsheet - {month} {year}.pdf",
    "HDFC MF Factsheet {month} {year}.pdf",
    "HDFC MF Factsheet - {month} {year} (1).pdf",
)
# Publication-folder offsets (in months after the as-of month) to try, in order.
_FACTSHEET_FOLDER_OFFSETS = (1, 2)


def _shift_month(as_of: date, months: int) -> str:
    total = as_of.year * 12 + (as_of.month - 1) + months
    return f"{total // 12}-{total % 12 + 1:02d}"


def factsheet_candidate_urls(as_of: date) -> list[str]:
    """Every URL the combined factsheet for `as_of`'s month may live at, most likely first."""
    urls = []
    for offset in _FACTSHEET_FOLDER_OFFSETS:
        folder = _shift_month(as_of, offset)
        for pattern in _FACTSHEET_NAME_PATTERNS:
            filename = pattern.format(month=as_of.strftime("%B"), year=as_of.year)
            urls.append(FILES_HOST + folder + "/" + urllib.parse.quote(filename))
    return urls


class HDFCAdapter:
    amc_id = "amc-hdfc"
    # Factsheets: one PDF per month for every scheme.
    factsheet_scope = "combined"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type == DocType.FACTSHEET:
            return self._list_factsheets(since, scheme_hint, client)
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

    def _list_factsheets(self, since: date, scheme_hint: str | None,
                         client: httpx.Client | None) -> list[DocumentRef]:
        """One combined PDF per month: the URL is the same whatever `scheme_hint` is."""
        headers = {"User-Agent": config.USER_AGENT, "Range": "bytes=0-0"}
        refs: list[DocumentRef] = []
        owns_client = client is None
        c = client or httpx.Client(timeout=30, follow_redirects=True)
        try:
            for as_of in _month_ends_since(since, date.today()):
                for url in factsheet_candidate_urls(as_of):
                    try:
                        resp = c.get(url, headers=headers)
                    except httpx.HTTPError:
                        continue  # transient; a later run re-probes this month
                    if resp.status_code in _PRESENT_STATUSES:
                        refs.append(DocumentRef(url=url, doc_type=DocType.FACTSHEET,
                                                as_of_date=as_of, scheme_hint=scheme_hint))
                        break
                    # _ABSENT_STATUS (403) means "no such key": try the next candidate.
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
