"""Groww Mutual Fund adapter.

Ground-truthed live 2026-09-13. AMFI's own registry names Groww's monthly disclosure page
directly: https://growwmf.in/statutory-disclosure/portfolio. That URL is a Next.js
*pages-router* SSR page (not a client SPA and not app-router `/_next/data` JSON) — a plain
`httpx.get()` with the honest `config.USER_AGENT` returns a full `200` HTML document, no
Cloudflare/anti-bot challenge on this host at all. Crucially, the page does not lazily fetch
its file listing over AJAX: the *entire* statutory-disclosure archive for every category
(Portfolio, AUM/AAUM, IDCW, Exposure Report, etc.) and every financial year is embedded
server-side in the page's own `<script id="__NEXT_DATA__" type="application/json">` tag as
one big nested `{folders: [{name, files: [{name, publicUrl}], folders: [...]}]}` tree
(`props.pageProps.filesData`). No listing API call, no Playwright capture, no reverse
engineering was needed — just parse that one script tag out of the already-fetched HTML.

Traversal: `filesData.folders` has one entry per statutory-disclosure category; find the one
named exactly `"Portfolio"`. Its own `.folders` are Indian-financial-year buckets
("2022- 2023", "2023- 2024", ..., "2026 -2027" — note the inconsistent spacing around the
hyphen, harmless since we don't parse the year-folder name itself, only iterate its `.files`).
Coverage goes back to March 2023 (Groww Mutual Fund's first disclosure after AMC registration;
it was formerly Indiabulls Mutual Fund — see the sheet-naming quirk below). Every month back to
inception is present in one page load: **no query parameter, no per-month iteration, no year
selector to drive** — question 4 (how history is reached) is answered by "it's all already
there."

Each year folder mixes Monthly and Fortnightly portfolio files (plus, on the current year,
occasionally an ad-hoc `.zip`) with NO consistent naming: files are hand-uploaded and the
"Monthly"/"Fortnightly" prefix has real typos in the wild — observed variants include
"Montlhy" (monthly, transposed), "Fortnighlty", "Forthnightly", "Fotnightly", and
"Fortnightly_Portfolio" (underscore instead of space). A literal `.startswith("Monthly")`
or a "fortnight" substring search silently drops some of these. Classification here instead
takes the filename prefix before the word "portfolio", strips it to bare letters, and picks
whichever of "monthly" / "fortnightly" has the smaller Levenshtein distance — this survives
every typo actually observed and should survive new ones too, since a genuine "monthly" typo
stays far closer to "monthly" than to "fortnightly" (11 letters vs ~7).

Date parsing is similarly typo/format-tolerant: filenames mix "Aug 31 2026", "Aug 31, 2026",
"July 31 2026" (unabbreviated), "30 April, 2026" (day-before-month), and even a bare
"Oct 2023" with no day at all. Since every MONTHLY_PORTFOLIO disclosure is, by regulation,
always an as-of *month-end* snapshot, the day token is never actually needed: we only extract
a 4-digit year and a 3-letter month prefix (case-insensitively, tolerant of "Sept" vs "Sep")
from the filename and compute the month-end date ourselves via `calendar.monthrange`, rather
than trusting (or requiring) the literal day text in the filename.

Files physically live on a separate host from the disclosure page: `publicUrl` values point at
`assets-netstorage.growwmf.in` (a CDN/blob store, not `growwmf.in` itself) and are fetched
directly with the same honest User-Agent — no separate host-blocking quirk was observed here
(unlike HDFC's split between a blocked `www` and an open `files.` bucket).

Each monthly file is a **combined workbook**, one sheet per scheme (63 sheets on the August
2026 file), with **no separate "Index"/lookup sheet at all** — same shape as Franklin
Templeton. The sheet name itself is a short legacy code (e.g. "IB01", "IB02" — "IB" for
Indiabulls, the AMC's name before Groww's 2023 acquisition/rebrand; codes are not
resequenced), and each sheet's own row 1 carries the full title as `"<code>-<scheme name>"`
(e.g. "IB01-Groww Large Cap Fund") in the first populated cell. `combined_workbook
.find_sheet_by_title` already handles this directly and needed zero changes: its substring
match (`target in title_l`) works even with the leading "IB01-" prefix still attached to the
title text. One extra housekeeping sheet, "XDO_METADATA" (an export-tool artifact, not a
scheme), is present on every file; it is harmless to leave unexcluded since its own row-1
text ("Version") never collides with a real scheme_hint, but adapters wiring this AMC into the
registry may still pass `exclude_sheet_names=("XDO_METADATA",)` for cleanliness.

Header wording is the common "% To Net Assets" / "ISIN" / "Name of Instrument" layout, values
are already fractional (Grand Total row states `1` exactly, not `100`), and the Grand Total
label is the plain "Grand Total" recognised by `xlsx_portfolio.py` out of the box — no
percentage-scale or label quirk, no shared-parser change needed at all.
"""
from __future__ import annotations

import calendar
import json
import re
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

DISCLOSURE_PAGE = "https://growwmf.in/statutory-disclosure/portfolio"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

_MONTH_PREFIXES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def _is_monthly(filename: str) -> bool:
    """Classify by edit-distance to "monthly"/"fortnightly" on the prefix before the word
    "portfolio" — tolerant of the real-world typos seen in this AMC's uploads (see module
    docstring)."""
    low = filename.lower()
    idx = low.find("portfolio")
    prefix = low[:idx] if idx >= 0 else low
    letters = re.sub(r"[^a-z]", "", prefix)
    if not letters:
        return False
    return _levenshtein(letters, "monthly") < _levenshtein(letters, "fortnightly")


def _parse_month_end(filename: str) -> date | None:
    """Every monthly disclosure is an as-of month-end snapshot by regulation; only the year
    and month need to be recovered from the (inconsistently formatted) filename."""
    low = filename.lower()
    year_m = re.search(r"(20\d{2})", low)
    if not year_m:
        return None
    year = int(year_m.group(1))
    month = None
    for word in re.findall(r"[a-z]+", low):
        if len(word) >= 3 and word[:3] in _MONTH_PREFIXES:
            month = _MONTH_PREFIXES[word[:3]]
            break
    if month is None:
        return None
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day)


def _find_portfolio_files(next_data: dict) -> list[dict]:
    folders = next_data.get("props", {}).get("pageProps", {}).get("filesData", {}).get("folders", [])
    portfolio = next((f for f in folders if f.get("name") == "Portfolio"), None)
    if portfolio is None:
        return []
    files: list[dict] = []
    for year_folder in portfolio.get("folders", []):
        files.extend(year_folder.get("files", []))
    files.extend(portfolio.get("files", []))
    return files


class GrowwAdapter:
    amc_id = "amc-groww"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(DISCLOSURE_PAGE, headers=headers, timeout=45, follow_redirects=True)
        resp.raise_for_status()

        m = _NEXT_DATA_RE.search(resp.text)
        if not m:
            return []
        try:
            next_data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []

        refs: list[DocumentRef] = []
        seen = set()
        for entry in _find_portfolio_files(next_data):
            name = entry.get("name") or ""
            url = entry.get("publicUrl") or ""
            if not name or not url:
                continue
            if not name.lower().endswith((".xlsx", ".xls")):
                continue  # skip the occasional .zip (fortnightly-only so far, but be safe)
            if not _is_monthly(name):
                continue
            as_of = _parse_month_end(name)
            if as_of is None or as_of < since:
                continue
            key = (url, as_of)
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
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=90)
        resp.raise_for_status()
        return resp.content
