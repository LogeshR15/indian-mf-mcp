"""Zerodha Mutual Fund adapter.

Ground-truthed live 2026-09-13 against the exact URL AMFI's registry names for this AMC:
https://www.zerodhafundhouse.com/resources/disclosures?source=footer

That page is a Next.js site, but the disclosure listing is NOT client-side-rendered and needs
no AJAX/Playwright reverse-engineering at all: it is server-side-rendered (`"gssp":true`,
`__N_SSP__`) with the entire props payload embedded verbatim as a single JSON blob in
`<script id="__NEXT_DATA__" type="application/json">...</script>`. A plain `httpx.get()` on
that one URL, followed by a `json.loads()` on the script body, yields:

  - `props.pageProps.initialReports`: a list of report sections, each keyed by an `id`. The
    one we need is `id == "portfolio-disclosures"`, whose `data` list in turn holds
    `id == "monthly-portfolio-disclosures"` (also `fortnightly-portfolio-disclosures` and
    `half-yearly-portfolio-disclosures`, not used here). Its `files` array is the *entire*
    archive in one page load — 360 monthly files spanning November 2023 through August 2026
    at ground-truth time, each `{id, name, url, modTs}` with `url` already a direct,
    fetchable link to a real `.xlsx` on `assets.zerodhafundhouse.com` (a separate static asset
    host from the `www` site; no auth/cookie/referrer needed — confirmed with a bare
    `httpx.get()` and the project's own `config.USER_AGENT`).
  - `props.pageProps.initialSchemes`: a `{schemeCode, name, ...}` list for every live scheme
    (e.g. `ZNFTY` -> "Zerodha Nifty 50 Index Fund"), used here only to resolve a human-readable
    `scheme_hint` to the short code the filenames actually use (see below) — no second request.

So: one page fetch gives the full listing AND the code/name map. History is therefore not a
"since" query against the site at all — the whole archive is already in hand every time; this
adapter just filters what it parses out of that one blob by date and by scheme.

**Filename quirks handled explicitly:**
  - The scheme identifier in each filename is Zerodha's own short *scheme code* (`ZNFTY`,
    `ZN250`, `ZE100`, ...), not the fund's full name — e.g. `"ZNFTY - Monthly Portfolio
    August 2026"`. A caller's `scheme_hint` is far more likely to be the human name (as with
    every other adapter), so it is resolved against `initialSchemes` first: an exact
    case-insensitive code match wins outright, otherwise the hint is matched (either
    direction, case-insensitive substring) against each scheme's full `name` to recover its
    code, and only files whose code matches are returned.
  - Spacing around the separating dash is inconsistent: `"ZNFTY - Monthly Portfolio ..."` vs
    `"ZEN50- Monthly Portfolio ..."` (no space before the dash) — handled by anchoring the code
    to a plain alnum run rather than requiring a fixed `" - "` delimiter.
  - Month/year text is inconsistent in both spacing and abbreviation: `"August 2026"`,
    `"Aug 2025"`, `"Sept 2025"`, and even double-spaced `"January  2025"` all appear across the
    archive. Parsed via a lookup table covering full names, 3-letter abbreviations, and the
    irregular 4-letter "Sept", after collapsing repeated whitespace.
  - The two oldest entries, `"Monthly Portfolio - November 2023"` and `"... - December 2023"`,
    predate Zerodha's split to one workbook per scheme: they carry no scheme-code prefix at
    all (a single combined workbook, presumably one worksheet per scheme back then). They
    cannot be attributed to a single scheme from the filename alone, so they are only ever
    returned when no `scheme_hint` is given, and — like every other AMC's combined-workbook
    files — a caller wanting scheme-level holdings out of them would need
    `combined_workbook.py`'s sheet-picking logic, not `xlsx_portfolio.py` directly. No
    combined-workbook change was needed for this adapter's own deliverables (the fixture used
    is a post-split, single-scheme file).

Each post-split file is a single-sheet workbook (sheet name = the scheme code, e.g. `"ZNFTY"`)
with header "% to NAV" (already covered by `xlsx_portfolio.py`'s existing "% to nav" header
alias) and a `"GRAND TOTAL (AUM)"` row whose %-to-NAV is already expressed as a plain fraction
(e.g. `1.0027`, not `100.27`) — no percentage-points rescaling needed, and no shared-parser
change of any kind was required.
"""
from __future__ import annotations

import calendar
import json
import re
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

DISCLOSURES_PAGE = "https://www.zerodhafundhouse.com/resources/disclosures?source=footer"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
_FILENAME_RE = re.compile(r"^(?P<code>[A-Za-z0-9]+)\s*-\s*Monthly Portfolio\s+(?P<rest>.+)$")

_MONTH_LOOKUP: dict[str, int] = {}
for _num in range(1, 13):
    _MONTH_LOOKUP[calendar.month_name[_num].lower()] = _num
    _MONTH_LOOKUP[calendar.month_abbr[_num].lower()] = _num
_MONTH_LOOKUP["sept"] = 9  # irregular 4-letter abbreviation seen in the archive


def _parse_month_year(text: str) -> tuple[int, int] | None:
    """Parse a loosely-formatted 'Month Year' tail (collapsing repeated whitespace, accepting
    full names, 3-letter abbreviations, or 'Sept') into (year, month)."""
    collapsed = " ".join(text.split())
    parts = collapsed.rsplit(" ", 1)
    if len(parts) != 2:
        return None
    month_str, year_str = parts
    if not year_str.isdigit():
        return None
    month_num = _MONTH_LOOKUP.get(month_str.strip().lower())
    if month_num is None:
        return None
    return int(year_str), month_num


def _fetch_page_data(client: httpx.Client | None) -> dict:
    headers = {"User-Agent": config.USER_AGENT}
    get = client.get if client is not None else httpx.get
    resp = get(DISCLOSURES_PAGE, headers=headers, timeout=45, follow_redirects=True)
    resp.raise_for_status()
    m = _NEXT_DATA_RE.search(resp.text)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def _resolve_scheme_code(scheme_hint: str, schemes: list[dict]) -> str | None:
    hint = scheme_hint.strip().lower()
    if not hint:
        return None
    # Exact code match wins outright (callers may already pass the short code).
    for s in schemes:
        code = str(s.get("schemeCode") or "")
        if code and code.lower() == hint:
            return code
    # Otherwise match the full scheme name (substring, either direction).
    for s in schemes:
        name = str(s.get("name") or "").lower()
        code = s.get("schemeCode")
        if not name or not code:
            continue
        if hint in name or name in hint:
            return code
    return None


class ZerodhaAdapter:
    amc_id = "amc-zerodha"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []

        data = _fetch_page_data(client)
        page_props = data.get("props", {}).get("pageProps", {})
        reports = page_props.get("initialReports", [])
        schemes = page_props.get("initialSchemes", [])

        files: list[dict] = []
        for report in reports:
            if report.get("id") != "portfolio-disclosures":
                continue
            for section in report.get("data", []):
                if section.get("id") == "monthly-portfolio-disclosures":
                    files = section.get("files", [])
                    break
            break

        target_code = None
        if scheme_hint:
            target_code = _resolve_scheme_code(scheme_hint, schemes)
            if target_code is None:
                # Hint given but unresolvable against the known scheme list: nothing to match.
                return []

        refs: list[DocumentRef] = []
        for f in files:
            name = f.get("name") or ""
            url = f.get("url") or ""
            if not url.lower().endswith(".xlsx"):
                continue
            m = _FILENAME_RE.match(name)
            if not m:
                # Pre-split combined files with no scheme-code prefix (Nov/Dec 2023): only
                # usable when the caller wants everything, not one specific scheme.
                if target_code is not None:
                    continue
                my = None
                for candidate_prefix in ("Monthly Portfolio",):
                    if name.strip().startswith(candidate_prefix):
                        my = _parse_month_year(name.split("-", 1)[-1])
                if my is None:
                    continue
                year, month = my
            else:
                code = m.group("code")
                if target_code is not None and code.upper() != target_code.upper():
                    continue
                my = _parse_month_year(m.group("rest"))
                if my is None:
                    continue
                year, month = my

            last_day = calendar.monthrange(year, month)[1]
            as_of = date(year, month, last_day)
            if as_of < since:
                continue
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
