"""Bandhan Mutual Fund adapter.

Ground-truthed live 2026-09-14. AMFI's own registry names Bandhan's disclosure page as
https://bandhanmutual.com/downloads/disclosures. That URL is a plain Create-React-App SPA
shell (`<div id="root"></div>` plus `/static/js/main.<hash>.js`) with no document links, no
`__NEXT_DATA__`-equivalent, nothing in the raw HTML at all. Discovery had to go through the
bundled JS.

**This AMC was previously recorded as blocked** ("application-layer payload encryption" —
see docs/amc-coverage.md), grouped with JM Financial and Mahindra Manulife on the theory that
all three share a fintech backend whose transactional API returns `{"data": "<base64
ciphertext>"}`. That verdict was reached by testing only the transactional/investor-services
tier. Re-tested per the Axis precedent (an AMC's bundled JS can expose an
encryption-status flag marking one API tier plaintext while another is encrypted) — and it
holds here too, though the discovery path looks different from Axis's:

Discovery, step by step:
  1. `bandhanmutual.com/static/js/main.<hash>.js` (11MB, one static asset, no Playwright) is
     obfuscated (hex-indexed string arrays, `_0x...` identifiers) but grep for literal URL
     fragments survives obfuscation regardless — obfuscators rewrite identifiers and control
     flow, not string *contents* that are used as literal fetch URLs. Searching it for
     `https?://` and `wp-json` turned up two hostnames: `pnservices.bandhanmutual.com` (its
     paths are literally named `.../investorservices/encdec` — the encrypted tier the original
     investigation found) and, separately, `cmsnew.bandhanmutual.com`, called with paths like
     `/wp-json/finance-api/v1/posts/monthly-factsheets` and `/wp-json/finance-api/v1/posts/faqs`.
  2. `cmsnew.bandhanmutual.com` is a WordPress install. Its *default* REST namespace is locked
     down site-wide: `GET /wp-json/` (and anything under `wp/v2/*`) 401s with
     `{"code":"rest_cannot_access","message":"DRA: Only authenticated users can access the REST
     API."}` (a security plugin, "DRA", gating the standard namespace). That 401 is real and
     would produce the same "encrypted/blocked" verdict as before if this were the surface
     tested. But `finance-api/v1` is a *separate, custom* REST namespace the CMS admin plugin
     registers with its own (public) permission callback — a `GET
     /wp-json/finance-api/v1/posts/<category-slug>` with the honest UA and no auth returns a
     clean `200` and real JSON. This is the same shape as the Axis finding (a plaintext CMS
     tier coexisting with a locked-down/encrypted one) even though the specific mechanism here
     is "separate REST namespace with its own permission callback" rather than an explicit
     `API_ENCRYPTION_STATUS_CMS` flag — the generalizable lesson (check for a plaintext content
     tier before concluding an AMC's payloads are all encrypted) is the same one.
  3. The category-slug path segment matches a taxonomy term (verified by trying several
     variants); `monthly-portfolio` is the one that resolves to category "Portfolio Summary" /
     sub-category "Monthly portfolio" (confirmed against the frontend's own route table in the
     bundle, which maps the site's `/downloads/monthly-portfolio` tab to this exact slug). A
     wrong/unrecognised slug does not 404 — it silently falls back to an unrelated "latest
     posts" list, which cost some trial and error before landing on the real value.
  4. **One single GET returns the entire archive, every year, no pagination** — the response is
     `{"status": "200", "data": [<one post per calendar year>], ...}`, each post's
     `acf_fields.financial_year` giving the year and `acf_fields.disclosure_files[]` giving one
     entry per uploaded file: `{document_name, month, document_link: {url, filename, ...}}`.
     Ground-truthed 2026-09-14: 8 posts, years 2018 and 2020-2026 (2019 is absent — a real gap,
     not queried-and-empty). Files live on `storage.googleapis.com` (a separate GCS bucket, not
     the CMS host), fetchable with the honest UA, no separate auth.

  How as-of dates are recovered: `month` is a reliable full month name on every entry (verified
  against all 124 entries in the live archive), but there is **no reliable year field per
  entry** — year comes from the *post*, not the file — and the day is **not safe to assume as
  month-end**: most entries are month-end, but some historical entries are a few days earlier
  (SEBI monthly disclosure timing quirks / last-business-day-of-month filings, e.g. "26
  February 2021", "26 March 2021"), and computing day-of-month for those instead of reading it
  would produce a fabricated date instead of the AMC's own stated one (contributing.md rule 1).
  So the day is extracted from `document_name`'s own free text by searching for a number
  immediately preceding whichever of {full month name, 3-letter abbreviation, zero-padded
  numeric month} actually appears next to it — this tolerates "31 December 2023", "as on
  28-feb-2025", "31st-july-2025", "as on 27-02-2026" and the outright typo "31 December 20211"
  (extra trailing digit; ignored because only the day token is parsed from text, year always
  comes from the post) uniformly. Verified: 124/124 real entries in the live archive parse a
  valid day this way with zero fallback needed. `document_name` itself is otherwise unreliable
  as a *classifier* — one March 2025 entry is titled "...Debt Fund Portfolio..." while its own
  URL and content are the Equity/Hybrid workbook (a genuine CMS data-entry error) — so
  `document_name` text is used only for the day-of-month, never to decide which workbook a file
  is, matching how `sheet_resolver` below always determines scheme membership from the
  workbook's own content instead.

  File shape and a major, real capability limit: Bandhan publishes (at least) two combined
  workbooks a month — one titled "Debt Fund Portfolio" covering its debt-oriented schemes, and
  one titled "Equity Hybrid Fund Portfolios" covering equity/hybrid ones (an "Arbitrage"-titled
  third family appears standalone 2021-2023, later folded into "Equity Hybrid"). Both are
  combined workbooks with **no Index sheet** and no single fixed title row — the scheme name is
  wherever the first non-label, non-code string cell falls in each sheet's own first ~6 rows
  (varies file to file: sometimes row 2 col 1, sometimes row 3 col 2, sometimes a stray
  parenthetical like "(Previously known as ...)" sits right after the true title). A local
  `_resolve_sheet` (below) scans generically for that first candidate rather than assuming a
  fixed row/column, filtering out (a) cells that look like an internal short code (no space,
  <=10 chars, e.g. "IDF002", "INDEX") and (b) cells that are template labels ("Portfolio as
  on...", "Net Assets as on...", "(Previously known as...") — this handles every layout variant
  observed without needing a per-era branch.

  **The Equity Hybrid Fund workbook cannot actually be ingested through this pipeline, at any
  date sampled** — it carries only a `Company / Industry / (% ) NAV` top-holdings-and-sector
  summary, with no ISIN and no per-security detail at all (confirmed on 2018, 2022, 2023 and
  2025 samples). `xlsx_portfolio.py`'s header detection requires an ISIN column (spec-mandated,
  not a bug to route around) and correctly refuses it: `parse_confidence` 0.0 on every sample
  tried. This is a genuine gap in what this plaintext CMS tier exposes for equity/hybrid
  schemes, not a parser deficiency — nothing in `xlsx_portfolio.py` was changed to chase it.

  **The Debt Fund workbook itself has a hard format-change boundary that bounds usable
  history**: every Debt Fund file from December 2018 through December 2024 uses the *same*
  ISIN-less summary shape (`Name / Rating / Total / Asset Quality` columns — an issuer-level
  rating-bucket breakdown, not a security-level table), and fails the same header-detection
  gate for the same honest reason. Starting exactly with the January 2025 file, the Debt Fund
  workbook switches to the full SEBI-standard `Name of the Instrument / ISIN / Industry-Rating /
  Quantity / Market Value / % to NAV / YTM` layout (confirmed December 2024 = old/ISIN-less,
  January 2025 = new/ISIN-complete, file size roughly quintuples the same month). So **usable,
  reconciling history through this adapter is January 2025 onward only** — a real capability
  limit dictated by what Bandhan itself started publishing, not a discovery shortfall. Every
  older Debt Fund file, and every Equity Hybrid Fund file at any date, is still surfaced by
  `list_documents` (nothing is hidden) and correctly falls out via the existing
  `skipped_reconciliation_failed` (old summary layout, ISIN missing) or
  `skipped_sheet_not_found` (scheme genuinely absent from that particular workbook) gates in
  `ingest_scheme_portfolios` — no special-casing was added here to filter them out in advance,
  the same policy Navi and 360 ONE use for their own pre-cutoff legacy files.

  Verified against real data: the January 2025 and August 2026 Debt Fund workbooks both
  reconcile at exactly 100% (`grand_total_pct_nav == 1.0`) with zero `xlsx_portfolio.py`
  changes; Bandhan Low Duration Fund's August 2026 sheet ("Bandhan LDF") yields 84 holdings
  including a real ISIN-complete one (7.45% Power Finance Corporation Limited,
  INE134E08NP7, 5.84% of NAV).

  Nothing on either host (`cmsnew.bandhanmutual.com` for discovery, `storage.googleapis.com`
  for files) challenges this project's honest `config.USER_AGENT` with a CAPTCHA, WAF block or
  anti-bot script — both answer a clean `200`.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime

import httpx
import openpyxl

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

API_URL = "https://cmsnew.bandhanmutual.com/wp-json/finance-api/v1/posts/monthly-portfolio"

# A sheet code/internal id is short and has no spaces ("IDF002", "INDEX", "IDFC LDF" is a
# *sheet name*, not a cell value, so that's not in play here); a scheme name is longer
# free-text with a space in it. Used only to skip non-title candidate cells.
_MAX_CODE_LEN = 10
_LABEL_PREFIXES = (
    "portfolio as on", "portfolio statement", "net assets as on", "previously known as",
)


def _find_scheme_title(rows: list[tuple]) -> str | None:
    """Scans a sheet's own first ~6 rows for the first string cell that looks like a scheme
    name rather than an internal code or a template label. No fixed row/column is assumed:
    observed layouts put the title anywhere from row 2 col 1 to row 3 col 2 depending on era."""
    for row in rows[:6]:
        for cell in row:
            if not isinstance(cell, str):
                continue
            t = cell.strip()
            if not t:
                continue
            tl = t.lower().lstrip("( ")
            if any(tl.startswith(p) for p in _LABEL_PREFIXES):
                continue
            if " " not in t and len(t) <= _MAX_CODE_LEN:
                continue  # looks like a short internal code (e.g. "IDF002", "INDEX")
            return t
    return None


def _resolve_sheet(raw: bytes, scheme_hint: str) -> str | None:
    """Local resolver (no Index sheet exists on either Bandhan combined workbook family):
    generic first-title-candidate scan, see `_find_scheme_title`."""
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    target = scheme_hint.strip().lower()
    best = None
    for sheet_name in wb.sheetnames:
        if sheet_name.strip().lower().startswith("disclaimer"):
            continue
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(min_row=1, max_row=6, values_only=True))
        title = _find_scheme_title(rows)
        if title is None:
            continue
        title_l = title.strip().lower()
        if title_l == target:
            return sheet_name
        if (target in title_l or title_l in target) and best is None:
            best = sheet_name
    return best


def _parse_as_of(document_name: str, month_name: str, year: int) -> date | None:
    """Year comes from the post (`financial_year`), never from `document_name` text, which
    carries real typos in the year (2-digit "23", a stray extra digit "20211"). Day is parsed
    from `document_name` by searching for a number immediately preceding whichever month token
    actually appears (full name, 3-letter abbreviation, or zero-padded numeric) -- never
    defaulted to month-end, since some real entries are a few days short of it (last-business-
    day-of-month filings). Verified 124/124 on the live archive (2026-09-14)."""
    try:
        month_num = datetime.strptime(month_name, "%B").month
    except ValueError:
        return None
    for token in (month_name, month_name[:3], f"{month_num:02d}"):
        m = re.search(rf"(\d{{1,2}})(?:st|nd|rd|th)?[\s\-]*{re.escape(token)}",
                       document_name, re.IGNORECASE)
        if m:
            try:
                return date(year, month_num, int(m.group(1)))
            except ValueError:
                continue
    return None


class BandhanAdapter:
    amc_id = "amc-bandhan"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []

        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(API_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "200":
            return []

        refs: list[DocumentRef] = []
        seen = set()
        for post in payload.get("data", []):
            acf = post.get("acf_fields", {})
            year_str = acf.get("financial_year")
            if not year_str:
                continue
            try:
                year = int(year_str)
            except ValueError:
                continue
            for item in acf.get("disclosure_files", []):
                link = item.get("document_link") or {}
                url = link.get("url")
                month_name = item.get("month")
                document_name = item.get("document_name", "")
                if not url or not month_name:
                    continue
                as_of = _parse_as_of(document_name, month_name, year)
                if as_of is None or as_of < since:
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
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
