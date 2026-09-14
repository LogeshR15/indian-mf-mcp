"""HSBC Mutual Fund adapter.

Ground-truthed live 2026-09-14. This AMC was previously recorded in docs/amc-coverage.md as
"No discoverable disclosure page", with the guess that the original attempt never actually
tried AMFI's own registered URL for it. That guess was correct, but the registered URL itself
turned out to be a false lead too — the real archive lives one click away, on a page AMFI does
not register at all.

**AMFI's registry names the wrong page.** Its `amc_monthly_portfolio_disclosure` entries for
HSBC all point at
`https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources` (plus
`Doc=fund-factsheets` / `Doc=other-disclosures` / `Doc=scheme-annual-report` query-string
variants). That page **is** plain server-rendered HTML — no SPA, no JS needed, confirmed live —
and it does embed its *entire* document archive inline (~4,000 PDFs + ~350 XLSX across every
disclosure category, one page load, no pagination). But none of the `Doc=` query values AMFI
registers do anything: the page ignores its own query string server-side and always renders
the same full, unfiltered archive (grep confirms zero `Doc=` or `module-17` references anywhere
in the response; filtering is a client-side-only checkbox widget over the same static list).
More importantly, **the SEBI-mandated monthly portfolio holdings XLSX simply is not on this
page at all.** Its "Portfolios" document-type category holds only ad-hoc PDFs (weekly debt
summaries, half-yearly unaudited statements, one static "Monthly portfolio with ISINs" PDF);
its "Fund factsheets" category is the glossy PDF factsheet ("The Asset"), not the regulatory
holdings statement. Searching every one of the ~30 document-type categories on this page for
an XLSX with per-instrument ISIN/quantity/%-NAV columns comes up empty.

**The real page is `investor-resources/information-library`** — a sibling of the registered
URL, linked from the site's own primary navigation but never surfaced in AMFI's registry. It
renders an accordion of ~20 sections ("Scheme Summary Document", "Fund dashboard", "AUM", ...);
the one that matters is **"Fund portfolios"**, whose content is a plain HTML `<table>` with one
row per (scheme, month) — `<a href="..." title="...">` — covering **every scheme, every month,
in one page load** (no AJAX, no pagination, ~2,200 file links in this one section alone). This
is the same "the whole archive is server-rendered inline" shape as Zerodha/Trust/360 ONE, just
via a plain HTML table instead of a JS data blob. A `Half Yearly Portfolios` and a `Fortnightly
Debt Portfolio` accordion section sit alongside it in the same page — excluded here by title
text ("fortnightly"/"half yearly"), since this adapter only serves `MONTHLY_PORTFOLIO`.

One quirk worth flagging for whoever re-tests this page: a plain `GET` on it occasionally times
out through this project's own outbound proxy (~20-90s, no response) and then succeeds instantly
(<1s) on the very next attempt with no code change at all — a transient proxy/connection issue
on this end, not a block on HSBC's side (`HEAD` on the same URL is consistently fast). Don't
record this page as blocked from a single timeout; retry once.

**File host and URL shape.** Links are Sitecore media-library paths, inconsistently rendered
across the archive with or without a leading slash and with inconsistent casing
(`-/media/Files/...` vs `/-/media/files/...`) — both resolve identically once the leading slash
is stripped and the path is re-joined to the site root (`www.assetmanagement.hsbc.co.in` itself;
no separate CDN/S3 host, unlike several other AMCs here). Filenames are hand-uploaded and
non-formulaic (`hsbc-flexi-cap-fund-31-aug-2026.xlsx` recently, `flexi-cap-fund-31012022.xlsx`
mid-archive, `hsbc-flexi-cap-fund.xlsx` for the oldest file with no date in the name at all) —
the URL is not computable, so this adapter always discovers rather than guesses it, and the
as-of date is always read from the row's own `title` text (`"HSBC Flexi Cap Fund 31 August 2026
(XLS, 225KB)"`, `"HSBC Aggressive Hybrid Fund as on 30 September 2023"`, both forms seen,
"as on" optional) rather than from the URL or the folder-ID-shaped path segment
(`document-DDMMYYYY/`) that sits in front of recent filenames — that segment is an upload-batch
ID, not the as-of date, confirmed by a folder stamped `document-07032023` containing a file
titled "... as on 31 August 2023" (a batch created in March 2023, later reused for an August
2023 upload). The oldest handful of entries per scheme (typically 2018-2021, scheme-launch-era)
carry no date in the title at all ("HSBC Flexi Cap Equity Fund", bare) — per spec rule #1
(never fabricate a fact that isn't stated), these are skipped rather than dated by guesswork,
which sets this adapter's real history floor per scheme rather than an artificial one.

**A second, real history-limiting quirk: HSBC renamed several schemes along the way**
("Flexi Cap Equity Fund" -> "Flexi Cap Fund", "ELSS Fund" -> "ELSS Tax Saver Fund", "Tax Saver
Equity Fund" -> presumably folded into ELSS, etc. — visible directly in the older titles
above), and `scheme_hint` matching here is a plain substring test against HSBC's *current*
naming (same limitation HDFC's adapter documents for its own case-sensitive scheme names).
Ground-truthed live: `scheme_hint="HSBC Flexi Cap Fund"` returns 46 documents, November 2022
(the month of the rename) through August 2026 — the older "Flexi Cap Equity Fund"-titled files
for the same underlying scheme, which the archive holds back to October 2021, are real and
present on the page but are not matched by the current name and are therefore not returned;
this is a real, not fabricated, coverage boundary rather than a bug to paper over with fuzzy
name-matching that could just as easily match the wrong scheme.

**Layout: one XLSX per scheme per month** (PPFAS/Mirae/DSP/Union-style, not a combined
workbook) — `sheet_resolver=None`.

**Three real, general parser gaps found and fixed in `xlsx_portfolio.py` (not handled locally,
because none of them can be — they are shared column/row-classification logic, and each is a
wording/shape variant plausible on other AMCs too, not an HSBC-only hack):**

1. HSBC spells its %-to-NAV header out in full — "Percentage to Net Assets" — instead of using
   "%" like every previously-registered AMC. `_find_main_header_row`'s `pct_col` needle list
   gained `"percentage to net"` / `"percentage to aum"` / `"percentage to nav"` /
   `"percentage of net"` alongside the existing `"%"`-prefixed forms.
2. HSBC's true grand-total row reads "Total Net Assets as on 31-August-2026" — a *prefixed*
   variant of the existing exact-match `_EXPLICIT_GRAND_TOTAL_LABELS` ("net assets", "total net
   assets") that Tata's bare "NET ASSETS" needed. The exact-match check was widened to a
   startswith check so the as-of-date suffix no longer defeats it (previously this row fell
   through to the generic `_is_aggregate_label` "total"-word skip and was silently dropped,
   correct-by-luck here only because the fallback self-summed total happens to equal it).
3. HSBC's files carry a SEBI-mandated "Scheme Riskometer" footer block (scheme name repeated as
   a row label, with the literal text "Scheme Riskometer" / "Scheme Benchmark Riskometer" in
   the industry/value cells) that `FOOTER_STOP_MARKERS`' prefix-based stop list can't catch —
   the label cell holds the scheme's own name, not a fixed footer phrase. Caught instead on the
   industry column's fixed "riskometer" text, which is not real industry/rating data anywhere.
   Debt-scheme files also print two loose quant-indicator lines ("Annualised Portfolio YTM !",
   "Macaulay Duration") with no "Quantitative Indicators" section header above them to trip the
   existing marker for that case; both were added to `FOOTER_STOP_MARKERS` directly.

Verified live: HSBC Flexi Cap Fund August 2026 (85 holdings, exact 100.00% reconciliation),
January 2022 (52 holdings, percentage-point scale correctly normalised, exact 100%), and HSBC
Corporate Bond Fund August 2026 (83 holdings after the quant-indicator-footer fix, exact 100%)
— three different months and both an equity and a debt scheme, with none of the parser
changes above regressing any other AMC's existing golden fixture (full `pytest -q` re-run
clean after each change).

Nothing about this host resembles a block: both `investor-resources` and
`investor-resources/information-library`, and every file link found on the latter, answer this
project's honest `config.USER_AGENT` with a clean `200` and no anti-bot challenge.
"""
from __future__ import annotations

import calendar
import html
import re
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

SITE_ROOT = "https://www.assetmanagement.hsbc.co.in"
INFO_LIBRARY_PAGE = f"{SITE_ROOT}/en/mutual-funds/investor-resources/information-library"

# Matches an <a> tag carrying both href and title regardless of attribute order — most of the
# archive renders href-then-title, but a handful of older rows (e.g. the ad-hoc combined
# "HLF, HUSDF, Y0AK and Y0BL as on 30 Dec 2022" files) render title-then-href instead.
_ANCHOR_RE = re.compile(
    r'<a\s+(?:href="(?P<href1>[^"]+)"\s+title="(?P<title1>[^"]+)"'
    r'|title="(?P<title2>[^"]+)"\s+href="(?P<href2>[^"]+)")[^>]*>'
)

# Same tolerant "day month year" reader used by other adapters here (Bajaj, ITI): matches
# "31 August 2026" and abbreviated "31 Dec 2021" alike, wherever it appears in the title text,
# so it naturally ignores a trailing "(XLS, 225KB)" size suffix.
_DAY_MONTH_YEAR_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})")
_MONTH_PREFIX = {calendar.month_abbr[m].lower(): m for m in range(1, 13)}


def _month_num(word: str) -> int | None:
    return _MONTH_PREFIX.get(word.strip().lower()[:3])


def _parse_as_of(title: str) -> date | None:
    m = _DAY_MONTH_YEAR_RE.search(title)
    if not m:
        return None
    day, month_word, year = m.groups()
    month_num = _month_num(month_word)
    if month_num is None:
        return None
    try:
        return date(int(year), month_num, int(day))
    except ValueError:
        return None


def _resolve_url(href: str) -> str:
    """Hrefs render inconsistently as `-/media/...` and `/-/media/...` across the archive;
    both resolve to the same Sitecore media path once re-joined to the site root."""
    return f"{SITE_ROOT}/{href.lstrip('/')}"


def _extract_fund_portfolio_rows(page_html: str) -> list[tuple[str, str]]:
    """Returns (href, title) pairs from the "Fund portfolios" accordion section only — the
    page also carries a sibling "Half Yearly Portfolios" and "Fortnightly Debt Portfolio"
    section with the same markup shape, so scoping to the boundary between this section's own
    heading and the next `<section id="tabpanel...">` avoids ever reading past it."""
    start = page_html.find("Fund portfolios")
    if start == -1:
        return []
    end = page_html.find('<section id="tabpanel', start)
    section = page_html[start:end] if end != -1 else page_html[start:]
    rows = []
    for m in _ANCHOR_RE.finditer(section):
        href = m.group("href1") or m.group("href2")
        title = html.unescape((m.group("title1") or m.group("title2")).strip())
        rows.append((href, title))
    return rows


class HSBCAdapter:
    amc_id = "amc-hsbc"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []

        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(INFO_LIBRARY_PAGE, headers=headers, timeout=90, follow_redirects=True)
        resp.raise_for_status()

        target = (scheme_hint or "").strip().lower()

        refs: list[DocumentRef] = []
        seen = set()
        for href, title in _extract_fund_portfolio_rows(resp.text):
            href_path = href.split("?")[0]
            if not href_path.lower().endswith((".xlsx", ".xls")):
                continue
            title_l = title.lower()
            if "fortnightly" in title_l or "half yearly" in title_l or "half-yearly" in title_l:
                continue
            if target and target not in title_l:
                continue
            as_of = _parse_as_of(title)
            if as_of is None or as_of < since:
                continue
            url = _resolve_url(href)
            key = (url.lower(), as_of)
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
