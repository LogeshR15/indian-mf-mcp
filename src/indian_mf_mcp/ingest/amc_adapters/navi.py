"""Navi Mutual Fund adapter.

Ground-truthed live 2026-09-13. AMFI's registry names this AMC's disclosure page as
https://navi.com/mutual-fund/downloads/portfolio. It is a WordPress + Elementor site, server-
rendered but with no static file links at all: the page renders a set of "Financial Year" /
"Month" `<select>` dropdowns (`data-category="884" data-type="Monthly" data-order="DESC"` for
the Monthly Portfolio panel; 885/886/887/928 are Fortnightly/HalfYearly/Quarterly/Overlap-Report
panels, not used here) and a jQuery `change` handler in the theme's own
`wp-content/themes/navi-theme-child/assets/js/app.js` drives an AJAX call on every
select-change. No Playwright was needed — the request shape is fully spelled out in that one
static JS asset:

    POST https://navi.com/wp-json/nv/v1/documents
    header: WP-NONCE: <value>
    body (form-encoded): financial_year=<YYYY-YYYY>, value=<MonthName>, category=884,
                          type=Monthly, order=DESC

`value` is the literal full month name ("August"), not a number. `financial_year` is the
Indian FY string the as-of month falls into (April Y..March Y+1 -> "Y-{Y+1}"; e.g. August 2026
-> "2026-2027"). Both `financial_year` and `value` are mandatory — the endpoint has no
"list everything" mode, so history is reached by iterating one request per calendar month from
`since` to today (REST 400s with `rest_missing_callback_param` if either is omitted; returns
`{"success": true, "data": []}` harmlessly for a month with nothing published, e.g. a future
month or an FY pre-dating the fund's launch).

The `WP-NONCE` header is a real, enforced check (omitting it gets a `403
Security verification required (nonce missing)`), but it is WordPress's standard anonymous
`wp_create_nonce`/`wp_verify_nonce` pair, which is time-window-based (valid ~12-24h) and
identical for every anonymous visitor — not a per-session or per-user secret, and not a
CAPTCHA/bot-detection token. It is openly embedded in the page's own inline script
(`var navi_property = {..., "nonce": "..."}`), so this adapter fetches the disclosure page
once per `list_documents` call with the same honest `config.USER_AGENT`, scrapes that value,
and reuses it for the batch of monthly POSTs that follow (well within the validity window).

Response `data[]` entries are `{"title": ..., "url": ...}` (occasionally `url` is a list of
`{link, ...}` for other document categories, but never observed for Monthly Portfolio, which
is always a plain string). Two file layouts are mixed across the years, both handled uniformly
since nothing here depends on the layout beyond a working URL:
  - Recent months (~2025 onward): one `.xlsx` per scheme, hosted on a separate CDN host
    (`public-assets.prod.navi-tech.in`), matching the one-file-per-scheme shape this project's
    shared parser already expects (Union/PPFAS-style).
  - Older months (~2021-2024): one `.xlsx`/`.xls` per scheme on a different host
    (`public-navi-docs.s3.ap-south-1.amazonaws.com`), and for a range of 2022-2024 months the
    URL has **no file extension at all** (e.g. `.../Navi%20Nifty%20India%20Manufacturing%20
    Index%20Fund_August`) despite serving a correct `Content-Type:
    application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` body — confirmed by a
    direct HEAD probe. This adapter never filters on file extension for exactly this reason;
    it trusts every URL the endpoint returns and lets the shared `sniff()` step in the ingest
    pipeline decide format from content, same as any other adapter.
  - Pre-2021 months publish one *combined* legacy-BIFF `.xls` for the whole AMC ("Monthly
    Portfolio- June 2020.xls") rather than one workbook per scheme. `xlsx_portfolio.py` only
    reads `.xlsx`; the shared ingest pipeline's `sniff()` step already skips
    (`skipped_format`) anything that isn't real XLSX rather than guessing from the extension,
    so these old entries are surfaced by `list_documents` (nothing here is fabricated or
    silently dropped) but are harmlessly skipped downstream without any special-casing in this
    adapter.

Title text needs HTML-entity unescaping before matching against `scheme_hint` (titles arrive
as e.g. `Navi Flexi Cap Fund 1st &#8211; 31st August 2026`, and one older month was seen with a
double-dash artifact `Navi &#8211;Nasdaq 100-Fund of- Fund`), and matching is done on a
whitespace-collapsed, lower-cased, space-stripped basis to absorb inconsistent spacing/hyphen
conventions across years ("Navi Large & Midcap Fund" vs "Navi Large and Midcap Fund").

One file per scheme per month (not a combined workbook, for the modern layout) — parses
against `xlsx_portfolio.py` with zero changes: exact 100% reconciliation, 86 holdings on the
August 2026 Navi Flexi Cap Fund file used for the fixture/tests here.
"""
from __future__ import annotations

import calendar
import html
import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

DOWNLOADS_PAGE = "https://navi.com/mutual-fund/downloads/portfolio"
DOCUMENTS_ENDPOINT = "https://navi.com/wp-json/nv/v1/documents"
MONTHLY_CATEGORY = "884"

_NONCE_RE = re.compile(r'navi_property\s*=\s*\{[^}]*?"nonce"\s*:\s*"([a-f0-9]+)"')


def _financial_year(year: int, month: int) -> str:
    """Indian FY string an (year, month) pair falls into: April..March."""
    start = year if month >= 4 else year - 1
    return f"{start}-{start + 1}"


def _months_since(since: date, until: date):
    year, month = since.year, since.month
    while (year, month) <= (until.year, until.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _fetch_nonce(client: httpx.Client | None) -> str:
    headers = {"User-Agent": config.USER_AGENT}
    get = client.get if client is not None else httpx.get
    resp = get(DOWNLOADS_PAGE, headers=headers, timeout=45, follow_redirects=True)
    resp.raise_for_status()
    m = _NONCE_RE.search(resp.text)
    if not m:
        raise RuntimeError("navi: could not locate navi_property.nonce on disclosure page")
    return m.group(1)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", "", html.unescape(text).lower())


class NaviAdapter:
    amc_id = "amc-navi"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []

        owns_client = client is None
        c = client or httpx.Client(timeout=30, follow_redirects=True)
        try:
            nonce = _fetch_nonce(c)
            headers = {"User-Agent": config.USER_AGENT, "WP-NONCE": nonce}

            target = _normalise(scheme_hint) if scheme_hint else ""
            refs: list[DocumentRef] = []
            seen = set()
            today = date.today()
            for year, month in _months_since(since, today):
                month_name = datetime(year, month, 1).strftime("%B")
                fy = _financial_year(year, month)
                try:
                    resp = c.post(
                        DOCUMENTS_ENDPOINT,
                        data={
                            "financial_year": fy,
                            "value": month_name,
                            "category": MONTHLY_CATEGORY,
                            "type": "Monthly",
                            "order": "DESC",
                        },
                        headers=headers,
                    )
                except httpx.HTTPError:
                    continue  # transient; a later run re-requests this month
                if resp.status_code != 200:
                    continue
                try:
                    payload = resp.json()
                except ValueError:
                    continue
                if not payload.get("success"):
                    continue

                last_day = calendar.monthrange(year, month)[1]
                as_of = date(year, month, last_day)
                for entry in payload.get("data", []):
                    url = entry.get("url")
                    title = entry.get("title") or ""
                    if not isinstance(url, str) or not url:
                        continue  # non-Monthly categories can return a list of {link,...}
                    if target and target not in _normalise(title):
                        continue
                    key = (url, as_of)
                    if key in seen:
                        continue
                    seen.add(key)
                    refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                             as_of_date=as_of, scheme_hint=scheme_hint))
            refs.sort(key=lambda r: r.as_of_date)
            return refs
        finally:
            if owns_client:
                c.close()

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
