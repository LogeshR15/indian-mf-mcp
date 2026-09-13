"""Trust Mutual Fund adapter.

Ground-truthed live 2026-09-13 against the exact URL AMFI's own registry names for this AMC
(`amc_monthly_portfolio_disclosure` field at
https://www.amfiindia.com/online-center/portfolio-disclosure):
https://www.trustmf.com/disclosures?activeTab=portfolio-disclosures

**That URL is a dead end by itself, and would have produced a wrong "blocked" verdict if
taken at face value.** The page is a client-side Vite/React SPA: the initial HTML is a bare
`<div id="root"></div>` plus a single bundled `/assets/index-*.js` — no server-side rendering,
no inline listing, nothing the `?activeTab=` query string alone exposes (the tab selection
happens entirely in JS after the bundle runs; a plain `httpx.get()` on the page shows nothing
because the content is click/route-gated, exactly the trap CONTRIBUTING.md warns about).

**Discovery mechanism** — reading the served JS bundle (no Playwright needed): the bundle
fetches its own runtime config from `/config.json` on the same host:

    GET https://www.trustmf.com/config.json
    -> {"PROTOCOL": "https", "VITE_COSMOSAPIURL": "www.trustmf.com/api/api/", ...}

giving the API base `https://www.trustmf.com/api/api/`. Every data-driven section of the site
(products, NAV, downloads, disclosures, ...) goes through one generic endpoint on that base,
`Trust/GetData` (POST), with a small "Cosmos"-style query-descriptor body identifying which
named server-side query to run:

    POST https://www.trustmf.com/api/api/Trust/GetData
    Content-Type: application/json; charset=UTF-8
    body: {
      "systemQueryFileName": "disclosuresweb.xml",
      "tagName": "GetDisclosureByType",
      "searchField": "", "searchValue": "",
      "sortField": "uploaddate", "sortDirection": "DESC",
      "replaceField": "_slug_", "replaceValue": "portfolio-monthly-disclosure"
    }
    -> {"resultSetArray": [{"title": "TRUSTMF Monthly Portfolio Report as on 31.08.2026",
                             "uploaddate": "9/9/2026 12:00:00 AM",
                             "fileurl": "https://trustmf.com/Content/2026/9/Monthly Port_....xlsx",
                             ...}, ...]}

The disclosure-type slug (`portfolio-monthly-disclosure`) was confirmed against the site's own
type taxonomy via a second call to the same endpoint with `tagName: "GetAllDisclosureTypesWeb"`
(no `replaceField`), whose response lists every disclosure type with its `slug` and
`parentslug` — `portfolio-monthly-disclosure` is the one whose `parentslug` is
`portfolio-disclosures`, i.e. exactly the tab named in AMFI's registered URL. That lookup call
is not needed at runtime since the slug is a stable identifier, not a per-request value; it is
hardcoded here as `DISCLOSURE_SLUG` and re-verifiable at any time via the same POST with
`GetAllDisclosureTypesWeb`.

No cookie, session token, Referer, or CSRF header of any kind is required for this POST —
verified cold, with only `config.USER_AGENT` set. Not blocked, not rate-limited, no WAF
challenge observed.

**History**: one call returns the *entire* archive in one shot — 67 entries back to February
2021 at ground-truth time (no `since`/pagination parameter needed or offered; `sortDirection:
DESC` is cosmetic only, this adapter re-sorts ascending itself and filters client-side by the
caller's `since`). This is a fixed, complete list, not a "latest month" endpoint — question 4
from CONTRIBUTING.md's site-structure checklist is answered up front.

**Where files live**: each `fileurl` in the response is `https://trustmf.com/...` — the bare
apex domain, *not* the `www.trustmf.com` frontend host, and it 307-redirects to `www.` (or, for
some entries, is already correct). A plain `httpx.get(..., follow_redirects=True)` with the
project's honest User-Agent handles this transparently and returns a clean `200`; no separate
CDN/S3 host is involved despite the different subdomain. Filenames frequently contain literal
spaces (e.g. `Monthly Port_20260909123835.xlsx`) — `httpx` sends these correctly unescaped in
the request line without any manual percent-encoding, verified against a live fetch.

**Date parsing**: never trust the filename (wildly inconsistent — `Monthly Port_<timestamp>`,
`Copy of Mont_<timestamp>`, `TRUSTMF-Monthly-Portfolio-Report-as-on-<date>`, sometimes with a
`-1`/`-002`/`_R`/`-wecompress.com_` suffix from whoever re-uploaded it). The response's own
`uploaddate` field is the *upload* timestamp, not the as-of date, and lags the as-of month
(e.g. the August 2026 portfolio was uploaded 9/9/2026). The as-of date is instead parsed from
each entry's own `title` text, which is completely regular: `"... as on DD.MM.YYYY"` — always
present, always this exact `DD.MM.YYYY` shape, for every entry seen across the whole archive.

**File format history — a real capability boundary, not a bug**: only March 2026 onward (6
entries at ground-truth time: March-August 2026) are genuine `.xlsx`. Everything from January
2026 back through February 2021 is legacy `.xls` (BIFF), *except* a short xlsx-again window
from February through August 2021 (an early period before the AMC apparently switched its
production tooling to legacy Excel and back). `.xls` entries are discovered correctly by this
adapter (they are real, fetchable files) but rejected downstream by `portfolio_ingest.py`'s
format sniff (`skipped_format`) exactly like any other AMC's legacy files — this adapter does
not special-case or convert them.

**Combined workbook, one sheet per scheme — with a sheet-naming convention that changed
mid-archive**, requiring a resolver that is *not* a drop-in for either shared helper in
`combined_workbook.py`:
  - There is no separate "Index"/lookup sheet at all (rules out `find_sheet_code`).
  - From March 2026 onward the sheet name itself already IS the human scheme name (e.g. sheet
    `"TRUSTMF Liquid Fund"`), so a plain sheet-name match suffices for those months.
  - For August 2021-January 2026 (and June-July 2026, which reverted to this style for a few
    schemes) sheets are named by terse internal acronyms instead (`"TMFLIQ"`, `"TMFFLEXI"`,
    `"TMFARB"`, ...) that bear no resemblance to the scheme name — same shape of problem as
    quant Mutual Fund's `_resolve_sheet`. Unlike quant, though, the real scheme name for these
    months sits in each sheet's own **second** populated row, column B (row 1 instead holds
    either the sheet's own acronym again or, on several sheets observed in the August 2026
    file, a stray leftover `"TMFCB"` literal that reads like a copy-paste artefact from a
    different scheme's template and must not be mistaken for this sheet's own identifier).
  - `_resolve_sheet` below distinguishes the two layouts with one heuristic that held for
    every sheet checked across both the March 2026 (full-name) and August 2026 (acronym) live
    files: an all-uppercase sheet name (`sheet_name.isupper()`) is treated as an acronym and
    the title is read from row 2 instead; anything else (mixed case, i.e. already a real name)
    is used as-is. This local variant is kept in this file rather than proposed as a shared-
    parser change, following the same precedent as quant's own local `_resolve_sheet` — no
    other AMC observed so far needs a sheet-name-dependent row to scan.

**Parser fit**: headers vary by month/style but both wordings observed
(`"% to Net Assets"` acronym-sheet style, `"% To Net Assets"` full-name-sheet style) are
already covered case-insensitively by `xlsx_portfolio.py`'s existing "% to net" alias; values
are already fractional (e.g. `0.0583` = 5.83%, not `5.83`); the grand-total row is a plain
"Grand Total"-equivalent row already recognised by the shared parser. Ground-truthed on the
August 2026 TRUSTMF Flexi Cap Fund sheet (`TMFFLEXI`): exact 100% reconciliation, 85 holdings,
zero shared-parser changes required.
"""
from __future__ import annotations

import io
import re
from datetime import date

import httpx
import openpyxl

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

API_URL = "https://www.trustmf.com/api/api/Trust/GetData"
# Confirmed (see module docstring) against the live GetAllDisclosureTypesWeb response: the
# type whose parentslug is "portfolio-disclosures" (the tab AMFI's registry names) is this slug.
DISCLOSURE_SLUG = "portfolio-monthly-disclosure"

_TITLE_DATE_RE = re.compile(r"as on\s+(\d{2})\.(\d{2})\.(\d{4})", re.IGNORECASE)


def _resolve_sheet(raw: bytes, scheme_hint: str) -> str | None:
    """Local variant of combined_workbook.find_sheet_by_title (see module docstring): sheets
    from March 2026 onward are named after the scheme itself; older/reverted months name
    sheets with a terse all-caps acronym and carry the real scheme name in row 2 instead."""
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    target = scheme_hint.strip().lower()
    best = None
    for sheet_name in wb.sheetnames:
        if sheet_name.isupper():
            ws = wb[sheet_name]
            try:
                row2 = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
            except StopIteration:
                continue
            title = next((c for c in row2 if isinstance(c, str) and c.strip()), None)
        else:
            title = sheet_name
        if title is None:
            continue
        title_l = title.strip().lower()
        if title_l == target:
            return sheet_name
        if (target in title_l or title_l in target) and best is None:
            best = sheet_name
    return best


class TrustAdapter:
    amc_id = "amc-trust"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT,
                   "Content-Type": "application/json; charset=UTF-8"}
        post = client.post if client is not None else httpx.post
        body = {
            "systemQueryFileName": "disclosuresweb.xml",
            "tagName": "GetDisclosureByType",
            "searchField": "", "searchValue": "",
            "sortField": "uploaddate", "sortDirection": "DESC",
            "replaceField": "_slug_", "replaceValue": DISCLOSURE_SLUG,
        }
        resp = post(API_URL, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            return []

        refs: list[DocumentRef] = []
        seen = set()
        for item in data.get("resultSetArray") or []:
            url = item.get("fileurl")
            title = item.get("title") or ""
            if not url:
                continue
            m = _TITLE_DATE_RE.search(title)
            if not m:
                continue
            day, month, year = (int(g) for g in m.groups())
            try:
                as_of = date(year, month, day)
            except ValueError:
                continue
            if as_of < since:
                continue
            key = url.split("?")[0]
            if key in seen:
                continue
            seen.add(key)
            refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                     as_of_date=as_of, scheme_hint=scheme_hint))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        # fileurl is the bare "trustmf.com" host (no "www"); it 307-redirects to
        # "www.trustmf.com" for some entries and is already correct for others.
        # follow_redirects handles both uniformly.
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=90)
        resp.raise_for_status()
        return resp.content
