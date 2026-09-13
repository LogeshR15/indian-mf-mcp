"""ICICI Prudential Mutual Fund adapter.

Previously recorded as blocked outright ("F5 BIG-IP WAF, TLS-fingerprint-based"). That verdict
was wrong in the same way HDFC's was: the WAF only ever affects the *listing* SPA's private
investor-portal API, never the public file host, and never via TLS/JA3 — the actual behavior
observed is plain CORS + a 401 from an authenticated endpoint, not a TLS fingerprint block.

## What actually happens

`www.icicipruamc.com` is a client-rendered React SPA (Vite build, no SSR — the raw HTML for
every route is an identical empty `<div id="root">` shell). The AMFI-registered downloads URL
(`/news-and-media/downloads?...`) 404s over plain httpx not because it is blocked, but because
the CDN returns a 404 *status* for any non-root path while still serving the SPA shell body —
completely harmless: a real browser renders it correctly via client-side routing, and the
whole page (including F5's `f5_cspm`/`TSbd` anti-bot cookie script, present even on the plain
200 homepage) is never a CAPTCHA and never denies this project's honest User-Agent.

The page's own document metadata (titles like "Monthly Portfolio Disclosure August 2026") is
fetched client-side from `apimf.icicipruamc.com/nms/v1/downloads/*` — an investor-portal API
that both lacks CORS headers for a plain cross-origin call AND 401s some sub-calls. That API
is a dead end and is NOT used here.

Ground-truthed live 2026-09-13 via a one-time browser capture (network/`window.open`
monkey-patch on the real page, mirroring the Sundaram method): the "Download" button for each
month's card does not call that API at all — it calls `window.open()` directly with a
pre-resolved static URL on `www.icicipruamc.com/blob/...`, a public Azure Blob Storage
container fronted by the same CDN (NOT the apimf host, no auth, plain 200 to this project's
honest User-Agent, `x-ms-*` / `x-azure-ref` headers confirm Azure Blob origin):

    https://www.icicipruamc.com/blob/downloads/Files/Monthly Portfolio Disclosures/
        <YYYY>/<MonFolder>/Monthly-Portfolio-Disclosure-<FullMonthName>-<YYYY>.zip

Both `<YYYY>` and the filename's month use the as-of month itself (unlike HDFC, no "publish
the following month" offset). `<MonFolder>` is NOT a consistent 3-letter abbreviation — it is
Jan/Feb/Mar/Apr/May/June/July/Aug/Sep/Oct/Nov/Dec: months whose 3-letter abbreviation would
save nothing (May) or is itself ambiguous/uncommon (June, July) are spelled out in full, while
every other month is abbreviated to 3 letters. Verified consistent across every month sampled
from June 2025 through August 2026, plus a cold (never-clicked) computed guess for January
2025 that also returned a real file — confirming the URL is genuinely computable, not just
copyable from an observed link. The site's own listing page shows only January 2025 onward
(20 entries) — earlier months 404 under this convention and are not probed further back.

Existence is checked with a plain `HEAD` (unlike HDFC's bucket, this Azure container answers
`HEAD` normally: 200 for a real key, 404 — a real 404, not a disguised AccessDenied — for an
absent one).

## Coarse file granularity — one zip per month, all schemes

Each monthly URL is a single ZIP (~20-25MB) containing one `.xlsx` per scheme for every scheme
the AMC runs that month, named after the scheme's own display name (e.g. "ICICI Prudential
Value Fund.xlsx", but some — ETFs mostly — omit the "ICICI Prudential" prefix or use different
casing, e.g. "BHARAT 22 ETF.xlsx", "ICICI PRUDENTIAL SILVER ETF.xlsx"). There is no per-scheme
URL; `list_documents` returns one `DocumentRef` per month (url = the zip), carrying
`scheme_hint` forward, and `fetch()` downloads that one zip and extracts only the member
matching `scheme_hint` (case-insensitive exact match on the member's basename, falling back to
substring containment either direction for near-miss naming). This mirrors the interface every
other adapter presents to `portfolio_ingest.py` (one call = one scheme's xlsx bytes) even
though network-wise it means re-fetching the same zip once per scheme tracked.

Each extracted worksheet ("DISCO") uses ICICI's own header wording — "Company/Issuer/Instrument
Name" rather than "Name of the Instrument" — which the shared parser's header-detection in
`xlsx_portfolio.py` does not currently recognise (`_find_main_header_row` only matches "name of
the instrument"/"name of instrument"). A one-line, purely-additive fix is needed; see the
shared-parser diff reported alongside this adapter. Once that lands, this file parses cleanly
with zero AMC-specific special-casing beyond the fix itself: fractional %-to-NAV convention
("% to Nav" column, already 0-1 scale), standard GRAND TOTAL row, 100% reconciliation.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

BLOB_HOST = "https://www.icicipruamc.com/blob/downloads/Files/Monthly Portfolio Disclosures"

# Month -> folder-name spelling actually used in the URL path. Not a uniform abbreviation:
# May/June/July are spelled out in full, every other month is 3 letters.
_MONTH_FOLDER = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "June",
    7: "July", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}
_MONTH_FULL = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


def build_url(as_of: date) -> str:
    """The monthly combined-workbook zip URL. Computable from the as-of month/year alone —
    no scheme name involved (all schemes ship in the one zip)."""
    folder = _MONTH_FOLDER[as_of.month]
    full_month = _MONTH_FULL[as_of.month]
    filename = f"Monthly-Portfolio-Disclosure-{full_month}-{as_of.year}.zip"
    return f"{BLOB_HOST}/{as_of.year}/{folder}/{filename}"


def _month_ends_since(since: date, until: date):
    import calendar
    year, month = since.year, since.month
    while True:
        last_day = calendar.monthrange(year, month)[1]
        candidate = date(year, month, last_day)
        if candidate > until:
            return
        if candidate >= since:
            yield candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _extract_scheme(zip_bytes: bytes, scheme_hint: str) -> bytes | None:
    target = scheme_hint.strip().lower()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xlsx")]
        exact = None
        loose = None
        for name in names:
            base = name.rsplit("/", 1)[-1][:-len(".xlsx")].strip().lower()
            if base == target:
                exact = name
                break
            if loose is None and (target in base or base in target):
                loose = name
        chosen = exact or loose
        if chosen is None:
            return None
        return zf.read(chosen)


class ICICIPrudentialAdapter:
    amc_id = "amc-icici-prudential"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO or not scheme_hint:
            return []

        headers = {"User-Agent": config.USER_AGENT}
        refs: list[DocumentRef] = []
        owns_client = client is None
        c = client or httpx.Client(timeout=30, follow_redirects=True)
        try:
            for as_of in _month_ends_since(since, date.today()):
                url = build_url(as_of)
                try:
                    resp = c.head(url, headers=headers)
                except httpx.HTTPError:
                    continue  # transient; a later run re-probes this month
                if resp.status_code == 200:
                    refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                             as_of_date=as_of, scheme_hint=scheme_hint))
                # 404 (a real 404 here, not a disguised AccessDenied): no file for this month.
        finally:
            if owns_client:
                c.close()
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        if not ref.scheme_hint:
            return resp.content
        extracted = _extract_scheme(resp.content, ref.scheme_hint)
        if extracted is None:
            raise ValueError(
                f"scheme {ref.scheme_hint!r} not found in monthly zip {ref.url}"
            )
        return extracted
