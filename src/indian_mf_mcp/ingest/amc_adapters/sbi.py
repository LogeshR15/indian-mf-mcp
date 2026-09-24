"""SBI Mutual Fund adapter.

Ground-truthed live 2026-09-12: unlike PPFAS (one file per scheme), SBI's disclosure page
(https://www.sbimf.com/en-us/portfolios) is a JavaScript SPA with no download links in the raw
HTML — the actual links come from a POST to a backing JSON/HTML endpoint,
`https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets`
(body `{"FundId":"","PSYear":"","PSMonth":"","PSFrequency":"Monthly"}`), which was found via a
one-time Playwright network-capture (not shipped at runtime — plain httpx POST reproduces it
exactly). Each returned file is a single combined workbook covering ALL ~120 SBI schemes, one
sheet per scheme plus an "Index" sheet mapping short-codes to scheme names — so this adapter
must fetch once per month and select the right sheet per scheme, rather than one file per scheme.

MONTHLY FACTSHEET discovery (ground-truthed live 2026-09-23)
-------------------------------------------------------------
No browser was needed. https://www.sbimf.com/factsheets is the same kind of SPA shell (no PDF
links in the raw HTML), but its served asset `/Content/Service/FactSheets.js` spells out the
request shape, and the site bundle (`/bundles/scripts?v=...`, `DataBindUrls`) names the URLs:

  * `POST /ajaxcall/CMS/GetFactSheets` with JSON `{"FundId":"","FSYear":"2026","FSMonth":"August"}`
    returns an HTML `<tr>` fragment, one row per PDF: `<td><a href="URL">TITLE</a></td>`.
    Same ajaxcall/CMS backend as the portfolio endpoint above, same honest-UA plain POST, no
    token (the `Token`-header encrypted API tier in that JS is only used for the fund-name and
    category dropdowns, not for the file listing). `FundId` "" and null behave identically
    (all rows for the month); an empty year/month returns "No Records Found".
  * `POST /ajaxcall/CMS/GetRecentFactSheets` (empty body) returns only the latest few months —
    not used, since GetFactSheets reaches everything it does and more.
  * `POST /ajaxcall/CMS/GetMonthsByYear` `{"folder":"Scheme Factsheets","year":"2026"}` lists the
    months that have files — handy for probing, not needed at runtime.

Files live on the same host: `https://www.sbimf.com/docs/default-source/scheme-factsheets/
<slug>.pdf?sfvrsn=<hash>` (Sitefinity). The slug is NOT computable across history (e.g.
`all-sbimf-schemes-factsheet-august-2026.pdf`, but June 2020 is plain `june-2020.pdf` and
2011-2015 are `february-2013.pdf`), so we always discover via the listing and date each file
by its listing TITLE, never its URL. The `sfvrsn` query is a cache-buster; the PDF also
serves without it, but we keep the URL exactly as listed.

What a month contains — SBI publishes COMBINED factsheets, but TWO of them:
  * "All SBIMF Schemes Factsheet <Month YYYY>" — every active scheme (equity/hybrid/FoF/debt,
    ~150 pages, ~11 MB). This is the one used for normal schemes.
  * A separate passives book: "SBI MF Passives (Index ETF FOF) Factsheet <Month YYYY>" (from
    April 2025) / "All SBIMF Schemes Factsheet (Index) <Month YYYY>" (Jan 2016 - Mar 2025).
    Index funds, ETFs, gold/silver FoFs appear ONLY here (verified against Aug 2026: "SBI
    Nifty Index Fund" / "Nifty 50 ETF" absent from the main book, "SBI Flexicap Fund" absent
    from the passives book).
  * Plus ~50-100 per-scheme extracts ("SBI Flexicap Fund  Factsheet June 2026"), which lag the
    combined books (Aug 2026 had none yet on 2026-09-23) and in 2020 carried no month in the
    title at all — so they are ignored; the combined books are the reliable monthly unit.
With a scheme_hint, one ref per month is returned: the passives book if the hint looks passive
(`_is_passive_scheme`: index/ETF/Nifty/Sensex/BSE/CRISIL/SDL/CPSE/gold/silver in the
normalised name — an inferred heuristic, checked against both Aug 2026 tables of contents),
else the main book. With scheme_hint=None both books are returned for each month.

History: iterate (year, month) from `since` to today, one POST per month. The main combined
book exists every month from January 2016; the passives book most months (a few, e.g. Mar 2017,
Jul/Oct 2018, Nov 2020, Dec 2024, have none listed). 2011-2015 hold only four books a year
(Feb/Apr/Aug/Dec), titled just "<Month YYYY>"; they are treated as the main book. Nothing
before April 2011. as_of_date is the month-end of the titled month.
"""
from __future__ import annotations

import calendar
import html as _html
import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef, normalize_scheme_name

PORTFOLIO_SHEETS_URL = "https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets"

_LINK_RE = re.compile(
    r'href="([^"]+all-schemes-monthly-portfolio---as-on-(\d{1,2})[a-z]{2}-([a-z]+)-(\d{4})\.xlsx[^"]*)"',
    re.IGNORECASE,
)

FACTSHEETS_URL = "https://www.sbimf.com/ajaxcall/CMS/GetFactSheets"
FACTSHEET_HISTORY_START = date(2011, 1, 1)  # earliest listed book is April 2011

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]

_FS_ROW_RE = re.compile(r'<td>\s*<a\s+href="([^"]+\.pdf[^"]*)"[^>]*>([^<]+)</a>\s*</td>', re.IGNORECASE)

# Titles of the combined books (see module docstring). Group "book": which book it is.
_COMBINED_TITLE_RE = re.compile(
    r"^(?:(?P<main>All\s+SBIMF\s+Schemes\s+Factsheet)"
    r"|(?P<passive>All\s+SBIMF\s+Schemes\s+Factsheet\s*\(Index\)"
    r"|SBI\s+MF\s+Passives\s*\(Index\s+ETF\s+FOF\)\s+Factsheet))?"
    r"\s*(?P<month>" + "|".join(_MONTHS) + r")\s+(?P<year>\d{4})$",
    re.IGNORECASE,
)

_PASSIVE_KEYWORDS = ("index", "etf", "nifty", "sensex", "bse", "crisil", "sdl", "cpse",
                     "gold", "silver")


def parse_factsheet_listing(html: str) -> list[tuple[str, str, date]]:
    """Parse one GetFactSheets HTML fragment into (url, book, as_of) for the COMBINED books only,
    where book is "main" or "passive" and as_of is the month-end of the titled month. Per-scheme
    extracts and anything unrecognised are dropped."""
    out: list[tuple[str, str, date]] = []
    seen: set[str] = set()
    for url, raw_title in _FS_ROW_RE.findall(html):
        title = re.sub(r"\s+", " ", _html.unescape(raw_title)).strip()
        m = _COMBINED_TITLE_RE.match(title)
        if not m:
            continue
        book = "passive" if m.group("passive") else "main"
        year = int(m.group("year"))
        month = [x.lower() for x in _MONTHS].index(m.group("month").lower()) + 1
        as_of = date(year, month, calendar.monthrange(year, month)[1])
        url = _html.unescape(url)
        if url.split("?")[0] in seen:
            continue
        seen.add(url.split("?")[0])
        out.append((url, book, as_of))
    return out


def _is_passive_scheme(scheme_hint: str) -> bool:
    key = normalize_scheme_name(scheme_hint)
    return any(k in key for k in _PASSIVE_KEYWORDS)


def _months_between(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


class SBIAdapter:
    amc_id = "amc-sbi"
    # Factsheets: two combined books per month (active / passives); page
    # scoping finds each scheme in whichever book carries it.
    factsheet_scope = "combined"

    def _headers(self) -> dict:
        return {
            "User-Agent": config.USER_AGENT,
            "x-requested-with": "XMLHttpRequest",
            "content-type": "application/json;charset=UTF-8",
            "referer": "https://www.sbimf.com/portfolios",
        }

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type == DocType.FACTSHEET:
            return self._list_factsheets(since, scheme_hint, client)
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []
        body = {"FundId": "", "PSYear": "", "PSMonth": "", "PSFrequency": "Monthly"}
        if client is not None:
            resp = client.post(PORTFOLIO_SHEETS_URL, json=body, headers=self._headers(), timeout=30)
        else:
            with httpx.Client() as c:
                resp = c.post(PORTFOLIO_SHEETS_URL, json=body, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        html = resp.text

        refs: list[DocumentRef] = []
        seen = set()
        for match in _LINK_RE.finditer(html):
            url, day, month_name, year = match.groups()
            try:
                as_of = datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y").date()
            except ValueError:
                continue
            if as_of < since:
                continue
            key = (url.split("?")[0], as_of)
            if key in seen:
                continue
            seen.add(key)
            # scheme_hint carries which sheet to select post-download (combined workbook,
            # not one file per scheme like PPFAS) — fetch() alone cannot know that; the
            # ingest orchestrator passes it through to the sheet-aware parser call.
            refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO, as_of_date=as_of,
                                     scheme_hint=scheme_hint))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def _list_factsheets(self, since: date, scheme_hint: str | None,
                         client: httpx.Client | None) -> list[DocumentRef]:
        headers = dict(self._headers(), referer="https://www.sbimf.com/factsheets")
        wanted = None if scheme_hint is None else (
            "passive" if _is_passive_scheme(scheme_hint) else "main")
        start = max(since.replace(day=1), FACTSHEET_HISTORY_START)

        def _run(c: httpx.Client) -> list[DocumentRef]:
            refs: list[DocumentRef] = []
            seen: set[tuple[str, date]] = set()
            for year, month in _months_between(start, date.today()):
                body = {"FundId": "", "FSYear": str(year), "FSMonth": _MONTHS[month - 1]}
                resp = c.post(FACTSHEETS_URL, json=body, headers=headers, timeout=30)
                resp.raise_for_status()
                for url, book, as_of in parse_factsheet_listing(resp.text):
                    if as_of < since or (wanted is not None and book != wanted):
                        continue
                    key = (url.split("?")[0], as_of)
                    if key in seen:
                        continue
                    seen.add(key)
                    refs.append(DocumentRef(url=url, doc_type=DocType.FACTSHEET, as_of_date=as_of,
                                            scheme_hint=scheme_hint))
            refs.sort(key=lambda r: (r.as_of_date, r.url))
            return refs

        if client is not None:
            return _run(client)
        with httpx.Client() as c:
            return _run(c)

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        if client is not None:
            resp = client.get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        else:
            with httpx.Client() as c:
                resp = c.get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content

    @staticmethod
    def find_sheet_code(index_sheet_rows, scheme_name: str) -> str | None:
        """Look up a scheme's sheet short-code from the workbook's own "Index" sheet, by
        substring match on the disclosed scheme name (spec: never hard-code a mapping that
        the source itself publishes and can change)."""
        target = scheme_name.strip().lower()
        for row in index_sheet_rows:
            name = row[2] if len(row) > 2 else None
            if isinstance(name, str) and (target in name.lower() or name.lower() in target):
                return row[1]
        return None
