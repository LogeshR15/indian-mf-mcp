"""Union Mutual Fund adapter.

Ground-truthed live: discovery via AMFI's own registry JSON
(https://www.amfiindia.com/online-center/portfolio-disclosure), which names Union's exact
monthly-disclosure URL directly (`amc_monthly_portfolio_disclosure`) —
https://www.unionmf.com/about-us/downloads/monthly-portfolio. That page is plain
server-rendered HTML (no JS-SPA problem, no Playwright/API reverse-engineering needed): every
month/scheme's file is embedded as a static inline-`<script>` JS array literal,
`downloadMonthPortfolio.push({Title: "...", Url: "...", DocYear: "..."})`, for the *entire*
archive back to 2021 in one page load. A plain `httpx.get()` + regex over that already-fetched
HTML yields every file URL directly — no separate API call.

Title format is inconsistent across years ("Union Flexicap Fund" with no date for some older
entries, "Monthly Portfolio Report Union Flexi Cap Fund 31-08-2026" for newer ones) — the date
is parsed from the URL's own `<month-name>-<year>/` path segment, which is consistently present,
rather than from the title text.

One file per scheme per month (PPFAS/Mirae/DSP-style layout, not a combined workbook) —
parses cleanly against xlsx_portfolio.py with zero changes (fractional-scale-detection
normalises the percentage-points convention used here, same as SBI/Motilal Oswal/Tata/etc.).
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

DOWNLOADS_PAGE = "https://www.unionmf.com/about-us/downloads/monthly-portfolio"

_ENTRY_RE = re.compile(r'Title:\s*"([^"]+)",\s*Url:\s*"([^"]+)"')
_URL_DATE_RE = re.compile(r"/([a-z]+)-(\d{4})/", re.IGNORECASE)


class UnionAdapter:
    amc_id = "amc-union"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(DOWNLOADS_PAGE, headers=headers, timeout=45, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        target = (scheme_hint or "").strip().lower()
        # Normalise "flexi cap"/"flexicap" spacing differences seen across years.
        target_compact = target.replace(" ", "")

        refs: list[DocumentRef] = []
        seen = set()
        for title, url in _ENTRY_RE.findall(html):
            url_path = url.split("?")[0]
            if not url_path.lower().endswith((".xlsx", ".xls")):
                continue
            title_l = title.lower()
            title_compact = title_l.replace(" ", "")
            if target and target not in title_l and target_compact not in title_compact:
                continue
            m = _URL_DATE_RE.search(url)
            if not m:
                continue
            month_name, year = m.groups()
            try:
                month_num = datetime.strptime(month_name, "%B").month
            except ValueError:
                continue
            import calendar
            last_day = calendar.monthrange(int(year), month_num)[1]
            as_of = date(int(year), month_num, last_day)
            if as_of < since:
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
