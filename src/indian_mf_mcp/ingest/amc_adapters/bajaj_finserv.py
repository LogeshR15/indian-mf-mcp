"""Bajaj Finserv Mutual Fund adapter.

Ground-truthed live 2026-09-13 against the exact URL AMFI's registry names for this AMC
(`amc_monthly_portfolio_disclosure` in https://www.amfiindia.com/online-center/portfolio-disclosure):
https://www.bajajamc.com/downloads?statutory-disclosures= — a plain server-rendered WordPress
page (theme "hello-elementor" + a custom "bajaj-downloads" plugin), not an SPA. It renders a
tabbed "Downloads" widget with one collapsible accordion per document category; none of the
accordions carry static file links in the raw HTML — instead each has a `data-section-id`
(an internal WordPress post/CPT id, e.g. `757` for the "Monthly Portfolio" accordion under the
"Portfolio" tab — found by string-searching the HTML for `bd-accordion-title">Monthly
Portfolio` and walking back to the nearest preceding `data-section-id`, never hardcoded, since
these ids are WordPress-internal and could be renumbered on a content edit) plus a
`data-filter="year_month"` attribute that drives a small first-party JS asset,
`https://www.bajajamc.com/wp-content/plugins/bajaj-downloads/assets/js/bajaj-downloads.js`
(read directly, no Playwright capture needed — the AJAX shapes are spelled out in that file
verbatim). That JS drives three plain WordPress `admin-ajax.php` actions, all reproducible
with plain `httpx`:

  1. `POST /wp-admin/admin-ajax.php` `action=bajaj_get_filter_options&filter_for=years&
     section_id=757` -> `{"options": [{"value": "2026-27", "label": "2026-27"}, ...]}` — a
     **fiscal-year** string (April-March), not a calendar year. Ground-truth: years go back to
     "2023-24", but the AMC's own first monthly portfolio only exists from April 2024 (the
     "2023-24" year returns zero documents for every month probed — the fund's schemes simply
     didn't exist yet). No standing capability limit here, just a young AMC.
  2. `POST /wp-admin/admin-ajax.php` `action=bajaj_get_filter_options&filter_for=months&
     section_id=757&year=<FY>` -> the list of calendar month names *actually published* for
     that fiscal year (e.g. the current, still-open FY returns only "April".."August", not all
     twelve) — so months are always fetched per-year rather than assumed to be a fixed 12.
  3. `POST /wp-admin/admin-ajax.php` `action=bajaj_get_downloads&section_id=757&year=<FY>&
     month=<MonthName>` -> an HTML fragment, one `<div class="bd-download-row">` per file, each
     with a `<span class="bd-download-title">...</span>` (free text ending "as on D Mon YYYY")
     and an `<a href="...">` to the real file, hosted on a separate asset host,
     `media.bajajamc.com` (not `www.bajajamc.com` — a distinct WordPress media/CDN host, same
     as the pattern documented for other AMCs where the file host and the portal are different
     infrastructure; here neither host is blocked, both serve this project's honest User-Agent
     a clean `200`).

**The as-of date is parsed from the download row's own title text** (`as on (\\d{1,2}) (\\w+)
(\\d{4})`), never reconstructed from the `year`/`month` request parameters: because `year` is a
*fiscal* year, "January"/"February"/"March" of FY "2025-26" is calendar year 2026, not 2025 —
reconstructing the date from the request params without that FY-to-calendar-year offset would
silently misdate every Jan/Feb/Mar file by a year. Parsing the title's own printed date sidesteps
the whole FY-arithmetic question.

**A WordPress AJAX nonce is required** (`action=...&nonce=<n>` on every one of the three POSTs
above) but **no cookie, session, or Referer is needed to obtain or use it** — verified by curling
every step cold, with no cookie jar at all, on the project's own honest User-Agent, and getting
well-formed JSON back every time. The nonce is embedded directly in the downloads page's own
inline `<script>` as `var bajajDownloads = {"ajaxUrl": "...", "nonce": "<n>"};`, read straight out
of the same page fetch that locates the `data-section-id`. (WordPress nonces of this kind are
time-windowed, not session-bound, for an anonymous/logged-out visitor — so a nonce lifted from
one page load is valid for every subsequent AJAX call made within the same `list_documents`
invocation.)

**File extension lies**: some months are served as `.xlsx`, others as `.xls` (e.g. August 2025's
file is literally named `...as_on_31_aug_2025.xls`) — but the `.xls`-named ones are still real
OOXML zip packages (`PK\\x03\\x04` magic, confirmed with `file(1)` reporting "Microsoft Excel
2007+" and a clean `zipfile.ZipFile` open), not legacy BIFF. This is exactly the "sites lie about
extension" case `parsers/sniff.py` already exists for: `sniff()` classifies purely on magic bytes,
and `openpyxl.load_workbook` is always called against an in-memory `io.BytesIO` (never a path
string), so the extension is never consulted anywhere in the pipeline and no special-casing was
needed for it.

**Layout: one combined workbook per month covering every scheme** (SBI/Tata/Sundaram-style, not
PPFAS/Union's one-file-per-scheme), 22 sheets as of August 2026, but with **no separate "Index"
lookup sheet at all** — like Franklin Templeton, the sheet's own key IS the scheme's short code
(e.g. `"BFFLX"`), and each sheet's own row 1 carries `(code, full scheme name)` in columns
0 and 1 (e.g. `('BFFLX', 'Bajaj Finserv Flexi Cap Fund', None, ...)`). This looks like
`combined_workbook.find_sheet_by_title`'s shape at first glance, but is NOT reused here: that
helper picks the *first populated string cell* in row 1 as the title, which for this AMC is the
*code* (column 0), not the name — and one sheet ("BFON", Bajaj Finserv Overnight Fund) even has a
corrupted code cell (literally the string `"hor"` instead of `"BFON"`), which would make a
first-string-cell heuristic actively wrong. `find_sheet_code` below instead reads column 1
specifically for the name and returns the workbook's own sheet key (never the possibly-corrupted
column-0 text) — a small, AMC-specific fix kept local to this file rather than generalizing the
shared helper.

Each sheet parses against `xlsx_portfolio.py` with **zero changes**: header row reads "Name of
the Instrument" / "ISIN" / "Industry" / "Quantity" / "Market/Fair Value\\n (Rs. in Lakhs)" /
"% to Net\\n Assets" (the embedded newline inside the header cell is harmless — the header
matcher does substring containment, not an exact string, so "% to net" still matches "% to
net\\n assets"), fractional %-to-NAV (e.g. 0.0643, not 6.43), and a plain "Grand Total" row.
Ground-truthed on the August 2025 combined workbook: `BFFLX` (Bajaj Finserv Flexi Cap Fund)
sheet reconciles to grand_total_pct_nav == 1.0 exactly, 90 holdings.
"""
from __future__ import annotations

import calendar
import io
import json
import re
from datetime import date

import httpx
import openpyxl

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

DOWNLOADS_PAGE = "https://www.bajajamc.com/downloads?statutory-disclosures="

_CONFIG_RE = re.compile(r"var\s+bajajDownloads\s*=\s*(\{.*?\});", re.DOTALL)
# Locate the "Monthly Portfolio" accordion's own data-section-id without hardcoding the id:
# walk forward from a `data-section-id="<n>" ... data-filter="year_month">` opener to the next
# accordion title, stopping if another data-section-id is hit first (i.e. never crossing into
# a different accordion).
_SECTION_ID_RE = re.compile(
    r'data-section-id="(\d+)"[^>]*data-filter="year_month">'
    r'(?:(?!data-section-id).)*?bd-accordion-title">Monthly Portfolio<',
    re.DOTALL,
)
_DOWNLOAD_ROW_RE = re.compile(
    r'bd-download-title">([^<]+)</span>.*?<a href="([^"]+)"', re.DOTALL
)
# Title text is free-form and inconsistent across months (see module docstring): "as on 31
# Jan 2024", "as 29 Feb 2024" (no "on"), "as on 31 March_2025" (underscore, no space), "as on
# 30 Septemeber 2023" (misspelled), and some months carry no "as on"/day at all, e.g. "Bajaj
# Finserv Mutual Fund – Monthly Portfolio October 2023". Two tiers below handle every case
# seen in the full archive (July 2023 through August 2026) without hardcoding a fixed phrase.
_DAY_MONTH_YEAR_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})")
_MONTH_YEAR_RE = re.compile(r"([A-Za-z]{3,})\s+(\d{4})")

# Matched on the first 3 letters only, lower-cased: tolerates full names, 3-letter
# abbreviations, and typos ("Septemeber" -> "sep") alike, with no explicit exception list.
_MONTH_PREFIX = {calendar.month_abbr[m].lower(): m for m in range(1, 13)}


def _month_num(word: str) -> int | None:
    return _MONTH_PREFIX.get(word.strip().lower()[:3])


def _parse_as_of(title: str) -> date | None:
    normalized = " ".join(title.replace("_", " ").split())
    m = _DAY_MONTH_YEAR_RE.search(normalized)
    if m:
        day, month_word, year = m.groups()
        month_num = _month_num(month_word)
        if month_num is not None:
            try:
                return date(int(year), month_num, int(day))
            except ValueError:
                pass
    # Fall back to a bare "Month YYYY" (no day at all) and use the calendar month-end, same
    # convention every other adapter here uses for a filename that only states month/year.
    m = _MONTH_YEAR_RE.search(normalized)
    if m:
        month_word, year = m.groups()
        month_num = _month_num(month_word)
        if month_num is not None:
            try:
                return date(int(year), month_num, calendar.monthrange(int(year), month_num)[1])
            except ValueError:
                pass
    return None


def _fetch_config(client: httpx.Client | None) -> tuple[str, str, str] | None:
    """Returns (ajax_url, nonce, section_id), or None if any could not be located."""
    headers = {"User-Agent": config.USER_AGENT}
    get = client.get if client is not None else httpx.get
    resp = get(DOWNLOADS_PAGE, headers=headers, timeout=45, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    cfg_match = _CONFIG_RE.search(html)
    if not cfg_match:
        return None
    try:
        cfg = json.loads(cfg_match.group(1))
    except json.JSONDecodeError:
        return None
    ajax_url, nonce = cfg.get("ajaxUrl"), cfg.get("nonce")
    if not ajax_url or not nonce:
        return None

    sid_match = _SECTION_ID_RE.search(html)
    if not sid_match:
        return None
    return ajax_url, nonce, sid_match.group(1)


def find_sheet_code(raw: bytes, scheme_hint: str) -> str | None:
    """Sheet-resolver for this AMC's combined workbook (no Index sheet; see module docstring
    for why `combined_workbook.find_sheet_by_title` is not reused: column 0 of a sheet's own
    row 1 is nominally the code but is corrupted on at least one sheet, "BFON" -> "hor"). Reads
    column 1 (the full scheme name) explicitly and returns the workbook's own sheet key, never
    the row's own (possibly wrong) column-0 text."""
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    target = scheme_hint.strip().lower()
    best = None
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        try:
            row0 = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        except StopIteration:
            continue
        if len(row0) < 2 or not isinstance(row0[1], str):
            continue
        name_l = row0[1].strip().lower()
        if not name_l:
            continue
        if name_l == target:
            return sheet_name
        if best is None and (target in name_l or name_l in target):
            best = sheet_name
    return best


class BajajFinservAdapter:
    amc_id = "amc-bajaj-finserv"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []

        cfg = _fetch_config(client)
        if cfg is None:
            return []
        ajax_url, nonce, section_id = cfg

        headers = {"User-Agent": config.USER_AGENT}
        post = client.post if client is not None else httpx.post

        years_resp = post(ajax_url,
                           data={"action": "bajaj_get_filter_options", "nonce": nonce,
                                 "section_id": section_id, "filter_for": "years"},
                           headers=headers, timeout=30)
        years_resp.raise_for_status()
        years_data = years_resp.json()
        if not years_data.get("success"):
            return []
        fiscal_years = [opt["value"] for opt in years_data["data"]["options"] if opt.get("value")]

        refs: list[DocumentRef] = []
        seen = set()
        for fy in fiscal_years:
            months_resp = post(ajax_url,
                                data={"action": "bajaj_get_filter_options", "nonce": nonce,
                                      "section_id": section_id, "filter_for": "months",
                                      "year": fy},
                                headers=headers, timeout=30)
            months_resp.raise_for_status()
            months_data = months_resp.json()
            if not months_data.get("success"):
                continue
            month_names = [opt["value"] for opt in months_data["data"]["options"] if opt.get("value")]

            for month_name in month_names:
                dl_resp = post(ajax_url,
                                data={"action": "bajaj_get_downloads", "nonce": nonce,
                                      "section_id": section_id, "year": fy, "month": month_name},
                                headers=headers, timeout=30)
                dl_resp.raise_for_status()
                dl_data = dl_resp.json()
                if not dl_data.get("success"):
                    continue
                html_fragment = dl_data["data"].get("html", "")
                for title, url in _DOWNLOAD_ROW_RE.findall(html_fragment):
                    if not url.lower().split("?")[0].endswith((".xlsx", ".xls")):
                        continue
                    as_of = _parse_as_of(title)
                    if as_of is None or as_of < since:
                        continue
                    key = (url.split("?")[0], as_of)
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
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
