"""Taurus Mutual Fund adapter.

Ground-truthed live 2026-09-13: AMFI's own registry JSON at
https://www.amfiindia.com/online-center/portfolio-disclosure names the exact monthly
disclosure URL for this AMC directly (`amc_monthly_portfolio_disclosure`):
https://taurusmutualfund.com/monthly-portfolio. A plain `httpx.get()` of that page (honest
User-Agent, no browser needed at all — not even Playwright for discovery, unlike Sundaram)
already returns server-rendered HTML, so no network capture was required here.

The site is Drupal 8 + Views + "Better Exposed Filters": the page shows a "Select Year" /
"Select Month" pair of `<select>` dropdowns (`field_monthly_portfolio_target_id` and
`field_month_target_id`, both underlying Drupal taxonomy-term IDs, not literal year/month
numbers) which submit as a plain GET to `/monthly-portfolio?field_monthly_portfolio_target_id=
<year-term-id>&field_month_target_id=<month-term-id>`. No JS/AJAX is involved in producing the
*results* — confirmed by fetching that GET URL with plain `httpx` (no Accept-Language/XHR
headers, no browser) and finding the same `<a href=".../downloads/Taurus_*.xlsx">` links in the
static response that a browser would render. Both dropdowns are required simultaneously —
leaving either as "All" (or querying month/year alone) yields zero result rows.

The term IDs are NOT a simple offset one could hardcode (year 2026 -> 567, 2025 -> 558, 2024 ->
514, ... down to 2012 -> 63 — irregular, presumably assigned as content nodes were added over
the years) so both maps are parsed from the base page's own `<option value="...">` HTML on
every discovery call, exactly like Union parses its inline JS array — never assumed. The month
map turned out to be a fixed, uneventful 281=January..292=December (a shared taxonomy
vocabulary independent of year), so `list_documents` iterates the (year value, month value)
combinations that fall inside `[since, today]` and issues one GET per candidate month; months
with nothing published yet (e.g. the current month before the disclosure is filed) return a
results page with zero `.xlsx` links, which is treated as "not yet published" rather than an
error.

One file per scheme per month (PPFAS/Union/Mirae/DSP-style layout, not a combined workbook),
named `Taurus_<Scheme_Slug>_Monthly_Portfolio_Report_Performance_<Month>_<Year>.xlsx` (e.g.
`Taurus_Flexi_Cap_Fund_Monthly_Portfolio_Report_Performance_August_2026.xlsx`) — both the
scheme name and the as-of month/year are read directly out of the filename via one regex
rather than out of link text or a request parameter, so this is robust even though the
`<a>` tag itself carries no other identifying text (just an Excel icon `<img>`). August 2026
(term ids year=567, month=288) was the latest live month found on 2026-09-13; September 2026
had a results page with zero links (not yet filed) — expected lag, not a break in discovery.

Parses cleanly against xlsx_portfolio.py with zero changes: standard "% to Net Assets" style
header, fractional (not percentage-points) NAV values, plain "Grand Total" row, ISIN column
present per row.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

BASE_PAGE = "https://taurusmutualfund.com/monthly-portfolio"

_YEAR_SELECT_RE = re.compile(
    r'id="edit-field-monthly-portfolio-target-id"[^>]*>(.*?)</select>', re.IGNORECASE | re.DOTALL
)
_MONTH_SELECT_RE = re.compile(
    r'id="edit-field-month-target-id"[^>]*>(.*?)</select>', re.IGNORECASE | re.DOTALL
)
_OPTION_RE = re.compile(r'value="(\w+)">([^<]+)</option>')
_FILE_RE = re.compile(
    r'href="\s*(/sites/default/files/downloads/Taurus_(.+?)_Monthly_Portfolio_Report_'
    r'Performance_([A-Za-z]+)_(\d{4})\.xlsx)"'
)


class TaurusAdapter:
    amc_id = "amc-taurus"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get

        resp = get(BASE_PAGE, headers=headers, timeout=45, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        year_sel = _YEAR_SELECT_RE.search(html)
        month_sel = _MONTH_SELECT_RE.search(html)
        if year_sel is None or month_sel is None:
            return []
        year_options = [(v, t) for v, t in _OPTION_RE.findall(year_sel.group(1)) if t.isdigit()]
        month_options = _OPTION_RE.findall(month_sel.group(1))

        today = date.today()
        target = (scheme_hint or "").strip().lower()
        target_compact = target.replace(" ", "").replace("_", "")

        refs: list[DocumentRef] = []
        seen = set()
        for year_value, year_str in year_options:
            year = int(year_str)
            if year < since.year or year > today.year:
                continue
            for month_value, month_name in month_options:
                try:
                    month_num = datetime.strptime(month_name.strip(), "%B").month
                except ValueError:
                    continue
                last_day = calendar.monthrange(year, month_num)[1]
                as_of = date(year, month_num, last_day)
                if as_of < since or as_of > today:
                    continue

                resp2 = get(
                    BASE_PAGE,
                    params={"field_monthly_portfolio_target_id": year_value,
                            "field_month_target_id": month_value},
                    headers=headers, timeout=45, follow_redirects=True,
                )
                resp2.raise_for_status()
                for path, scheme_slug, file_month, file_year in _FILE_RE.findall(resp2.text):
                    scheme_name = scheme_slug.replace("_", " ")
                    scheme_name_l = scheme_name.lower()
                    scheme_name_compact = scheme_name_l.replace(" ", "")
                    # scheme_hint commonly carries the "Taurus" prefix (e.g. "Taurus Flexi Cap
                    # Fund") while the filename-derived name never does (just "Flexi Cap
                    # Fund") -- check containment in both directions, not just hint-in-name.
                    if target and not (
                        target_compact in scheme_name_compact or scheme_name_compact in target_compact
                    ):
                        continue
                    try:
                        file_month_num = datetime.strptime(file_month, "%B").month
                    except ValueError:
                        file_month_num = month_num
                    file_year_int = int(file_year)
                    file_last_day = calendar.monthrange(file_year_int, file_month_num)[1]
                    file_as_of = date(file_year_int, file_month_num, file_last_day)
                    url = f"https://taurusmutualfund.com{path}"
                    key = (url, file_as_of)
                    if key in seen:
                        continue
                    seen.add(key)
                    refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                             as_of_date=file_as_of, scheme_hint=scheme_hint))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
