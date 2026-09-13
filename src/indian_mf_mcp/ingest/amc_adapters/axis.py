"""Axis Mutual Fund adapter.

Axis was previously recorded as blocked outright ("real API is same-origin but only reachable
after client-side JS the site fingerprints and blocks in Playwright specifically"). That verdict
was about a *browser* automation route, and it never needed to be true: the disclosure listing
page (https://www.axismf.com/statutory-disclosures) is a Next.js app whose portfolio-document
list is populated by a plain, unauthenticated-by-secret, unencrypted JSON API on the *same*
origin — reachable directly with `httpx` and the project's honest User-Agent, no browser, no
Playwright, no fingerprinting concern at all.

Ground-truthed live 2026-09-13 by reading the page's bundled Next.js chunks (no listing HTML
scrape needed — the chunk `/_next/static/chunks/183-e4aafc913713cfe1.js` contains the whole
statutory-disclosures React module in the clear):

  - The site's own API-client config sets `API_ENCRYPTION_STATUS: "enable"` for its aggregator
    (transactional) endpoints but `API_ENCRYPTION_STATUS_CMS: "none"` for everything under
    `/cms/*` — CMS traffic (which includes the disclosure documents) is deliberately left
    unencrypted, plaintext JSON in, plaintext JSON out. This is the load-bearing fact: no
    payload crypto to reverse-engineer, unlike some other AMCs' encrypted-payload APIs.
  - Auth is a single unauthenticated bootstrap call: `POST /cms/token` with an empty JSON body
    (`{}`) returns `{"data": {"token": "Bearer <opaque>"}}` — no login, no API key, no captcha.
    That bearer token is then sent as the `Authorization` header on the document-list call.
  - Document discovery is a two-step call to `POST /cms/get-scheme-documents` (same endpoint
    used for narrowing *and* fetching — the fields present in the body determine which):
      1. `{"sdType": "yearMonthSchemeDocs", "sdID": "sdMonthSchemePortfolio"}` returns
         `schemeCategories` (`[{schemeName, schemeCode}, ...]`, one of which is
         `{"schemeName": "Consolidated", "schemeCode": "Consolidated"}` — skipped here, it is
         an all-schemes combined file, not a single-scheme one), `years`, `months`, and
         `latestDocumentMonth`.
      2. Adding `"year"`, `"month"`, `"schemeCode"` to that same body returns `documentList`:
         `[{"docuementURL": "...", "documentName": "...", "documentPostedDate": "YYYY-MM-DD"}]`
         (note the AMC's own typo, `docuementURL` — copied verbatim, it is a real API field
         name, not a bug here). An absent month returns `{"data": {"message": "Data not
         available"}}` (still HTTP 200) rather than an error or empty list — handled below.
      3. The returned `docuementURL` is a same-origin `https://www.axismf.com/1/5/.../*.xlsx`
         file path, fetched with a plain `GET` and the honest User-Agent — no auth header, no
         token needed for the file itself, only for the two `/cms/*` listing calls above.

  - `sdID` naming: `"sdMonthSchemePortfolio"` is not guessable from the page's own top-level
    JSON blob — that blob only names the parent category (`"sdId": "sdPortfolios"`, type
    `"nestedList"`); its child node's id had to be resolved with one extra call to
    `POST /cms/get-nested-list` with `{"sdParentID": "sdPortfolios"}`, which lists
    `sdMonthSchemePortfolio` (monthly scheme portfolios — used here) and
    `sdFortnightlyPortfolio` (fortnightly debt-scheme disclosures — out of scope for
    `MONTHLY_PORTFOLIO`). Both ids are stable constants going forward; no need to re-discover
    them at runtime.
  - The request bodies are schema-validated server-side and reject unexpected fields outright
    (`{"status": "failure", ..., "error": "Extra fields detected: ..."}`) — so only the exact
    fields documented above are ever sent, nothing extra "for safety".

Coverage: `years` goes back to 2012 for every scheme; `latestDocumentMonth` at ground-truth
time was "September" while the newest actual document was the August 2026 snapshot (posted
under the *current* month, unlike HDFC's "folder = as-of month + 1" convention — here the
month/year queried is the as-of month itself, not the publication month).

One file per scheme per month (PPFAS/Union/HDFC-style, not a combined workbook). Parses
against xlsx_portfolio.py with zero changes: GRAND TOTAL reconciles to 1.0 exactly on the
August 2026 Axis Midcap Fund file, "% to Net Assets" already fractional (0.044, not 4.4),
matching the convention xlsx_portfolio.py already normalises for other AMCs.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

SERVICE_URL = "https://www.axismf.com"
TOKEN_URL = SERVICE_URL + "/cms/token"
DOCUMENTS_URL = SERVICE_URL + "/cms/get-scheme-documents"

# Resolved once via POST /cms/get-nested-list {"sdParentID": "sdPortfolios"} — stable constant.
SD_ID_MONTHLY_SCHEME_PORTFOLIO = "sdMonthSchemePortfolio"

_MONTH_NAMES = list(calendar.month_name)[1:]  # ["January", ..., "December"]

# The AMC's own field name typo — copied verbatim, this really is the API's field name.
_URL_FIELD = "docuementURL"


def _get_token(client: httpx.Client) -> str:
    headers = {"User-Agent": config.USER_AGENT, "Content-Type": "application/json"}
    resp = client.post(TOKEN_URL, headers=headers, json={}, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]["token"]


def _post_documents(client: httpx.Client, token: str, body: dict) -> dict:
    headers = {
        "User-Agent": config.USER_AGENT,
        "Content-Type": "application/json",
        "Authorization": token,
    }
    resp = client.post(DOCUMENTS_URL, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _months_since(since: date, until: date):
    year, month = since.year, since.month
    while (year, month) <= (until.year, until.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


class AxisAdapter:
    amc_id = "amc-axis"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO or not scheme_hint:
            return []

        owns_client = client is None
        c = client or httpx.Client(timeout=30, follow_redirects=True)
        refs: list[DocumentRef] = []
        try:
            token = _get_token(c)

            base_body = {"sdType": "yearMonthSchemeDocs", "sdID": SD_ID_MONTHLY_SCHEME_PORTFOLIO}
            listing = _post_documents(c, token, base_body)
            data = listing.get("data") or {}
            categories = data.get("schemeCategories") or []

            hint_l = scheme_hint.strip().lower()
            scheme_code = None
            for cat in categories:
                name = (cat.get("schemeName") or "").strip()
                if name.lower() == hint_l:
                    scheme_code = cat.get("schemeCode")
                    break
            if scheme_code is None:
                for cat in categories:
                    name = (cat.get("schemeName") or "").strip().lower()
                    if name != "consolidated" and (hint_l in name or name in hint_l):
                        scheme_code = cat.get("schemeCode")
                        break
            if scheme_code is None:
                return []

            today = date.today()
            seen = set()
            for year, month_num in _months_since(since, today):
                month_name = _MONTH_NAMES[month_num - 1]
                body = {
                    "sdType": "yearMonthSchemeDocs",
                    "sdID": SD_ID_MONTHLY_SCHEME_PORTFOLIO,
                    "year": str(year),
                    "month": month_name,
                    "schemeCode": scheme_code,
                }
                try:
                    resp = _post_documents(c, token, body)
                except httpx.HTTPError:
                    continue
                doc_data = resp.get("data") or {}
                for doc in doc_data.get("documentList") or []:
                    url = doc.get(_URL_FIELD)
                    if not url or not url.lower().endswith((".xlsx", ".xls")):
                        continue
                    as_of = None
                    posted = doc.get("documentPostedDate")
                    if posted:
                        try:
                            as_of = datetime.strptime(posted, "%Y-%m-%d").date()
                        except ValueError:
                            as_of = None
                    if as_of is None:
                        # Fall back to the requested (year, month) at month-end.
                        last_day = calendar.monthrange(year, month_num)[1]
                        as_of = date(year, month_num, last_day)
                    if as_of < since:
                        continue
                    key = (url, as_of)
                    if key in seen:
                        continue
                    seen.add(key)
                    refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                             as_of_date=as_of, scheme_hint=scheme_hint))
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
