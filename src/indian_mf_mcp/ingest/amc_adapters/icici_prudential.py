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

## Monthly factsheets (DocType.FACTSHEET)

Ground-truthed live 2026-09-23. Discovery order: (1) a public web search for
`icicipruamc.com blob downloads factsheet pdf` surfaced one real URL,
`.../blob/downloads/Files/Historic Factsheets/2024-2025/Complete Factsheet December 2024.pdf`;
(2) cold computed HEAD probes of that shape (financial-year folder + "Complete Factsheet
<FullMonth> <YYYY>.pdf") returned 200 for most months but 404 for a few the site plainly lists;
(3) a one-time Playwright capture (honest `config.USER_AGENT`, build-time only) of the
"Historical Factsheets" tab (`/media-center/downloads?currentTabFilter=HistoricalFactsheets`,
filter "Complete Factsheet") showed the tab is fed by `POST apimf.icicipruamc.com/nms/v1/
downloads/files` (categoryName HISTORICAL_FACTSHEET), whose JSON carries each file's blob path.
Paging that same call in the captured browser session yielded the full archive: 267 entries,
Jan 2001 -> Aug 2026. That API is NOT usable at runtime — a plain httpx POST with the honest UA
(no browser pre-login session) is routed to the Azure blob origin and answers
`405 UnsupportedHttpVerb` — so, like the portfolio path, runtime discovery is computed URLs +
HEAD probes against the public blob host, which answers 200 / real 404 to the honest UA.

URL convention (host = the same public Azure-Blob container as the portfolio ZIPs):

    https://www.icicipruamc.com/blob/downloads/Files/Historic Factsheets/<FY>/<filename>.pdf

`<FY>` is the Indian financial year "YYYY-YYYY" (April-March) of the as-of month. The filename
convention changed several times as the archive was manually uploaded, so no single pattern
works; `factsheet_candidate_urls()` yields every pattern seen in the ground-truth listing, in
most-recent-era-first order, and `list_documents` takes the first that HEADs 200:

    Complete Factsheet <FullMonth> <YYYY>.pdf     Apr 2024 onward (current convention)
    Complete Factsheet <Mon> <YYYY>.pdf           alternate FYs 2004-05 .. 2021-22; Jan+Feb 2025
    complete-factsheet-for-<fullmonth>-<yyyy>.pdf Apr 2018-Mar 2020, Aug 2022-Feb 2024
    complete-factsheet-for-<mon>-<yyyy>.pdf       2001-2017 (alternating with the above)
    complete-Factsheet-for-<fullmonth>-<yyyy>.pdf March 2024 only (capital F)
    complete-factsheet-for-<fullmonth>.pdf        Apr-Jun 2022 only (no year in the name)

(Patterns alternate roughly by financial year, and a few months break their FY's pattern, so the
adapter never assumes an era: it tries all six, current convention first.) Verified: all 266
monthly entries in the captured listing are reproduced by `factsheet_candidate_urls()`, and a
full live HEAD sweep Jan 2000 -> Sep 2026 found a file for every listed month except March 2023,
whose listed URL is itself a dead link on ICICI's site (Azure `BlobNotFound`, 404) — no
alternative spelling exists, so that month is simply absent.

History reach: January 2001 -> latest (August 2026 as of 2026-09-23, uploaded 2026-09-07),
~300 months; a few early months (e.g. Mar 2002) and Mar 2023 have no file.

Quirks: (a) April 2026's file sits in the *previous* FY folder (2025-2026) while May 2026 onward
sits in 2026-2027 — so candidates also try the previous FY folder as a fallback. (b) The site's
own listing omits every July (all years 2001-2025) and December 2025, yet they all HEAD 200
under the same conventions — a listing bug (the Month filter metadata has a duplicated
"September" / sortOrder 8 where July should be), not missing files; the probe finds them.
(c) Also listed but NOT returned here: a one-off "Mid Month Complete Factsheet Mar 2012", and the
separate Passive and Abridged factsheet series (in the Aug 2026 complete factsheet, index funds/ETFs
appear only in the cross-scheme annexures, not with their own scheme page — their scheme pages
are in the separate Passive factsheet). (d) Cost: one HEAD per month
for current-convention months; a month with no file costs up to 12 HEADs (6 names x 2 FY folders; 8 for May).
A live sweep from 2012 took ~18s.

Granularity: ONE combined PDF per month (~10-13MB, all active schemes). The URL does not depend
on `scheme_hint`; it is carried through on the ref and the caller scopes pages to the scheme.
Per-scheme PDFs also exist under `/blob/knowledgecentre/factsheet-schemes/...` but only for the
*latest* month (fixed, undated filenames, apparently replaced in place each month), so they
give no history and are not used. `as_of_date` = last day of the month the factsheet describes (the filename month;
the API's `applicableMonth` confirms the filename month is the as-of month, not the publish
month — August 2026's file was uploaded 2026-09-07). Scheme pages title the scheme by its
*current* name with the old one beneath, e.g. p.15 of Aug 2026: "ICICI Prudential Flexi Cap
Fund" / "(Erstwhile ICICI Prudential Flexicap Fund)" (renamed w.e.f. 2026-08-26).
"""
from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef, normalize_scheme_name

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


FACTSHEET_HOST = "https://www.icicipruamc.com/blob/downloads/Files/Historic Factsheets"


def _financial_year(year: int, month: int) -> str:
    start = year if month >= 4 else year - 1
    return f"{start}-{start + 1}"


def factsheet_candidate_urls(as_of: date) -> list[str]:
    """Every URL shape the ground-truth archive listing uses for a complete factsheet, most
    likely first. The caller HEADs them in order and keeps the first 200. See module docstring
    for which era used which pattern."""
    full = _MONTH_FULL[as_of.month]
    abbr = full[:3]
    y = as_of.year
    names = [
        f"Complete Factsheet {full} {y}.pdf",
        f"Complete Factsheet {abbr} {y}.pdf",
        f"complete-factsheet-for-{full.lower()}-{y}.pdf",
        f"complete-factsheet-for-{abbr.lower()}-{y}.pdf",
        f"complete-Factsheet-for-{full.lower()}-{y}.pdf",
        f"complete-factsheet-for-{full.lower()}.pdf",
    ]
    fy = _financial_year(y, as_of.month)
    prev_fy = _financial_year(y - 1, as_of.month)
    urls: list[str] = []
    for folder in (fy, prev_fy):  # prev FY: April 2026 was filed under 2025-2026
        for name in names:
            u = f"{FACTSHEET_HOST}/{folder}/{name}"
            if u not in urls:
                urls.append(u)
    return urls


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
    target = normalize_scheme_name(scheme_hint)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".xlsx")]
        exact = None
        loose = None
        for name in names:
            # ICICI's member names drift month to month ("Flexicap Fund." in Jul 2026, "Flexi
            # Cap Fund" in Aug 2026), so compare on the normalised key, not raw text.
            base = normalize_scheme_name(name.rsplit("/", 1)[-1][:-len(".xlsx")])
            if not base:
                continue
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
    # Factsheets: one PDF per month for every scheme.
    factsheet_scope = "combined"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type == DocType.FACTSHEET:
            return self._list_factsheets(since, scheme_hint, client)
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

    def _list_factsheets(self, since: date, scheme_hint: str | None,
                         client: httpx.Client | None) -> list[DocumentRef]:
        # One combined PDF per month for all schemes: the URL ignores scheme_hint, which is
        # only carried forward on the ref.
        headers = {"User-Agent": config.USER_AGENT}
        refs: list[DocumentRef] = []
        owns_client = client is None
        c = client or httpx.Client(timeout=30, follow_redirects=True)
        try:
            for as_of in _month_ends_since(since, date.today()):
                for url in factsheet_candidate_urls(as_of):
                    try:
                        resp = c.head(url, headers=headers)
                    except httpx.HTTPError:
                        continue  # transient; a later run re-probes this month
                    if resp.status_code == 200:
                        refs.append(DocumentRef(url=url, doc_type=DocType.FACTSHEET,
                                                 as_of_date=as_of, scheme_hint=scheme_hint))
                        break
        finally:
            if owns_client:
                c.close()
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        if ref.doc_type == DocType.FACTSHEET:
            get = client.get if client is not None else httpx.get
            resp = get(ref.url, headers=headers, follow_redirects=True, timeout=120)
            resp.raise_for_status()
            return resp.content  # raw combined PDF; no per-scheme extraction
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
