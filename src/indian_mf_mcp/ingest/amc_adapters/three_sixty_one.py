"""360 ONE Mutual Fund adapter (formerly IIFL Asset Management).

Ground-truthed live 2026-09-13 against the exact URL AMFI's own registry names for this AMC
(`amc_monthly_portfolio_disclosure` field at
https://www.amfiindia.com/online-center/portfolio-disclosure, `mf_id "62"`):

    https://www.360.one/asset/mutual-funds/downloads/

**What renders the listing.** This is a Next.js App Router site using React Server Components
streaming, not the older `__NEXT_DATA__` script tag (see `zerodha.py` for that shape). The
whole page's data model — every tab, every disclosure category, every year, every file —
arrives inline in the initial HTML response as one or more
`<script>self.__next_f.push([1,"<chunk>"])</script>` tags. Each chunk's second array element
is a JSON-encoded string; once `json.loads()`'d, it starts with a small numeric/hex id prefix
("4:", "17:", etc.) followed by a JSON array or object literal that is otherwise completely
well-formed JSON (React Flight protocol references like `"$Lb"` are just plain strings from a
JSON parser's point of view, so no bespoke Flight-format parser is needed). No AJAX call, no
Playwright capture, and no separate API endpoint were needed at all — one `httpx.get()` on the
downloads page is the entire discovery mechanism. `_iter_rsc_chunks()` walks every such push
chunk, strips the leading id prefix, and attempts `json.loads()` on what remains; chunks that
aren't themselves valid JSON (most of them — React component trees, import maps, etc.) are
silently skipped rather than assumed to be the one we want.

**Locating the payload.** Rather than hardcode the array index/path to the disclosures data
(fragile against any reshuffle of the page's own component tree), `_find_monthly_portfolio_subcategory()`
recursively scans the parsed JSON for any dict with a `"subcategories"` list containing an
entry whose `"title"` is exactly `"Monthly Portfolio"`. That entry's shape is:

    {"title": "Monthly Portfolio", "yearlyData": [
        {"year": "Monthly Portfolio 2026", "monthlyData": [
            {"month": "June", "documentGroups": [
                {"title": "Monthly Portfolio 2026", "documents": [
                    {"fileName": "June", "fileUrl": "https://s3.ap-south-1.amazonaws.com/x-web-s3.360.one/IN_MF_MONTHLY_PORTFOLIO_June2026_Final_8ded2027a1.xls"}
                ]}
            ]}
        ]}
    ]}

**Where the files physically live.** A separate host from the marketing site: every file URL
is `https://s3.ap-south-1.amazonaws.com/x-web-s3.360.one/<opaque-name>.xls[x]` — a public S3
bucket, not `www.360.one` itself. It answers a plain `GET` with the project's honest
`config.USER_AGENT` with a clean `200`; no separate probing/host-fallback logic was needed
the way HDFC needed (this bucket's listing is reachable through the downloads page itself, so
there is no "compute the URL" step at all — every file is already named for us).

**How the URL encodes scheme/date.** It doesn't, and isn't computable — filenames are an
inconsistent, human-curated grab-bag across nine years (`IN_MF_MONTHLY_PORTFOLIO_May2026_Final_e1cbdb4542.xlsx`,
`IIFLAMC_IN_MF_MONTHLY_PORTFOLIO_December_2018_Online_0f53538103.XLS`,
`MONTHLY_PORTFOLIO_June_2018_IIFLAMC_Mutual_Funds_web_download_ce2ad04ebc.xls`, each with a
distinct random hex suffix). The as-of month/year is therefore never parsed from the URL:
it comes from the JSON structure itself — `fileName` is always a clean, unabbreviated month
name ("January".."December", confirmed for every one of the 104 monthly entries in the
archive, no exceptions), and the year is parsed out of the enclosing `yearlyData[].year`
string ("Monthly Portfolio 2026" -> 2026) since `month` inside `monthlyData[]` is sometimes
the literal string `"null"` (2026's two most recent months, August and July, are grouped
together under one `monthlyData` entry with `"month": "null"` rather than one entry each —
harmless here since `fileName` is authoritative, but worth knowing if this shape changes).

**How history is reached.** The entire archive is server-rendered into this one page load —
no year selector, no query parameter, no pagination. `yearlyData` covers "Monthly Portfolio
2018" through "Monthly Portfolio 2026" (104 monthly documents total at ground-truth time) in
one `httpx.get()`. This adapter filters that in-hand list by `since` rather than requesting a
narrower range from the site.

**One combined workbook per month, not one file per scheme** (SBI/Sundaram/Franklin-style, not
PPFAS/Union/HDFC-style): each monthly `.xlsx` has one worksheet per scheme (e.g. "Flexicap
Fund", "Dynamic Bond", "Liquid Fund", "GOLDETF", "Overnight", ...). There is no separate
"Index" lookup sheet mapping short codes to full scheme names the way SBI/Motilal
Oswal/Tata/Nippon have — instead, exactly like `franklin_templeton.py`, each sheet's own row 1
carries the full scheme name as free text (e.g. "360 ONE FLEXICAP FUND - An Open Ended Dynamic
Equity Scheme investing across large cap, mid cap and small cap stocks" in column B of the
"Flexicap Fund" sheet). `combined_workbook.find_sheet_by_title()` (already shared, no changes
needed) resolves a caller's `scheme_hint` against that title row. Whoever wires this adapter
into `registry.py` should pass `combined_workbook.find_sheet_by_title` as the `sheet_resolver`.

**Extension is not a reliable format signal — verified, not assumed.** Some recent files are
served with a `.xls` extension and `application/vnd.ms-excel` best-guess S3 content-type while
actually being real ZIP-based `.xlsx` payloads (e.g. the August/July 2026 files) — confirmed
byte-for-byte (`PK\\x03\\x04` magic) before relying on it. This project's `sniff()` already
detects format from magic bytes rather than trusting the extension/Content-Type (spec §7), so
`fetch()` here does no extension-based filtering at all; every listed URL is returned and left
to the shared `ingest_scheme_portfolios` pipeline to sniff and skip if unparseable.

**Genuinely legacy files exist and are correctly parsed via parse_portfolio_xls, not skipped.**
Every file from "Monthly Portfolio 2020" and earlier (through 2018) is real legacy BIFF
(`D0 CF 11 E0` compound-document magic, confirmed on the January 2018 file, whose OLE metadata
even records the original author and IIFL-era filenames like
"IIFLAMC_MF_Portfolio_Jan_2018_download..." — IIFL Asset Management was 360 ONE's name before
its 2023 rebrand). `sniff()` correctly reports these as `XLS_BIFF`; since parse_portfolio_xls
and combined_workbook.find_sheet_by_title both learned to handle that format (xlrd, alongside
openpyxl for the newer `.xlsx` months), this project's earlier "no BIFF parser yet, real
capability limit" note no longer holds. Verified live end-to-end against this adapter's real
archive: `ingest_scheme_portfolios` for "Dynamic Bond Fund" ingests 103 of 104 discovered
documents back to January 2018 (the lone skip is a genuine sheet-not-found gap, not a format
failure) — zero `skipped_format`, zero reconciliation failures, no shared-parser or
adapter-side changes needed beyond the format-dispatch layer itself.

Verified end-to-end against the real May 2026 combined workbook, "Flexicap Fund" sheet:
`GRAND TOTAL` reconciles to exactly `1.0`, 55 holdings, header "Rounded % to Net Assets" (an
existing recognised header alias — no `xlsx_portfolio.py` change needed), values already
fractional (not percentage-points). Zero shared-parser changes were required for this AMC.
"""
from __future__ import annotations

import calendar
import json
import re
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

DOWNLOADS_PAGE = "https://www.360.one/asset/mutual-funds/downloads/"

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.DOTALL)
_PREFIX_RE = re.compile(r"^[0-9a-fA-F]+:")
_YEAR_RE = re.compile(r"(\d{4})")

_MONTH_LOOKUP = {calendar.month_name[i].lower(): i for i in range(1, 13)}


def _iter_rsc_chunks(html: str):
    """Yield every JSON-parseable payload embedded in this page's React-Server-Components
    streaming script tags. Most chunks are not JSON at all (component trees, import maps) and
    are silently skipped -- callers search whatever chunks do parse for the shape they want."""
    for m in _PUSH_RE.finditer(html):
        try:
            s = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        body = _PREFIX_RE.sub("", s, count=1).lstrip()
        if not body[:1] in "[{":
            continue
        try:
            yield json.loads(body)
        except json.JSONDecodeError:
            continue


def _find_monthly_portfolio_subcategory(node):
    """Recursively search the parsed payload for the 'Monthly Portfolio' subcategory dict,
    wherever it happens to be nested -- avoids depending on a fixed array index/path that
    would break if the page's own component tree is reshuffled."""
    if isinstance(node, dict):
        subs = node.get("subcategories")
        if isinstance(subs, list):
            for sub in subs:
                if isinstance(sub, dict) and sub.get("title") == "Monthly Portfolio":
                    return sub
        for v in node.values():
            found = _find_monthly_portfolio_subcategory(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_monthly_portfolio_subcategory(item)
            if found is not None:
                return found
    return None


class ThreeSixtyOneAdapter:
    amc_id = "amc-360-one"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(DOWNLOADS_PAGE, headers=headers, timeout=45, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        subcat = None
        for chunk in _iter_rsc_chunks(html):
            subcat = _find_monthly_portfolio_subcategory(chunk)
            if subcat is not None:
                break
        if subcat is None:
            return []

        refs: list[DocumentRef] = []
        seen = set()
        for yearly in subcat.get("yearlyData", []):
            ym = _YEAR_RE.search(str(yearly.get("year", "")))
            if not ym:
                continue
            year = int(ym.group(1))
            for monthly in yearly.get("monthlyData", []):
                for group in monthly.get("documentGroups", []):
                    for doc in group.get("documents", []):
                        file_name = str(doc.get("fileName") or "").strip()
                        url = doc.get("fileUrl") or ""
                        if not url:
                            continue
                        month_num = _MONTH_LOOKUP.get(file_name.lower())
                        if month_num is None:
                            continue  # unrecognised label; never seen in the live archive
                        last_day = calendar.monthrange(year, month_num)[1]
                        as_of = date(year, month_num, last_day)
                        if as_of < since:
                            continue
                        key = (url, as_of)
                        if key in seen:
                            continue
                        seen.add(key)
                        # One combined workbook per month covering every scheme: scheme_hint
                        # is carried through only as metadata for the sheet_resolver step
                        # downstream (combined_workbook.find_sheet_by_title), not filtered here.
                        refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                                 as_of_date=as_of, scheme_hint=scheme_hint))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
