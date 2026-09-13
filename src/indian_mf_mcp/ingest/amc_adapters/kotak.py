"""Kotak Mahindra Mutual Fund adapter.

Kotak was previously recorded as blocked ("Radware Bot Manager CAPTCHA"). That verdict is
correct for `www.kotakmf.com` itself, but wrong as a verdict on the AMC's *files* — same
lesson as HDFC: the listing host and the file host are different machines with different
rules.

  - `www.kotakmf.com` (the disclosure listing page AND the bare homepage) is genuinely and
    fully behind Radware Bot Manager: every request — even the homepage — comes back as an
    HTTP 302 to `validate.perfdrive.com` with a `server: rdwr` header and inline
    `__uzdbm_*`/`stormcaster.js` challenge JS, to this project's honest User-Agent, no
    exceptions found. Ground-truthed live 2026-09-13. This is a real, whole-domain block
    (unlike HDFC, where only the listing page itself was blocked and the homepage was fine) —
    that host is never used here.
  - `vatseelabs-s3.kotakmf.com` is a separate public CloudFront-fronted S3 bucket
    (`server: CloudFront`, `x-amz-server-side-encryption` headers) carrying every investor
    document — SID/KIM PDFs, factsheets, and monthly portfolio workbooks — under
    `/FormsDownloads/...`. It applies no bot-manager rule at all: this project's honest
    User-Agent gets a clean `200` and the real `.xlsx`. Found via a public web search for a
    real Kotak factsheet/portfolio filename (`vatseelabs-s3.kotakmf.com` turned up directly in
    search results for `site:kotakmf.com filetype:xlsx monthly portfolio`), then confirmed live.

Discovery is fully computable from the as-of date alone (no scheme name needed at all, unlike
HDFC — see below for why a scheme name still matters at *fetch* time):

    https://vatseelabs-s3.kotakmf.com/FormsDownloads/Portfolios/
        Consolidated-SEBI-Portfolio-as-on-<Month>-<D>,-<YYYY>/
        ConsolidatedSEBIPortfolio<Month><YYYY>.xlsx

where `<Month>` is the full English month name, `<D>` is the as-of day (month-end, NOT
zero-padded), and both the folder and filename use the as-of month itself (no month-after
offset the way HDFC's S3 bucket does). Verified live for August/July/June/May 2026 (`200`);
April 2026 and everything earlier returns `403 AccessDenied` (S3 denies `ListBucket`, so an
absent key reports `AccessDenied` rather than `NoSuchKey` — same as HDFC's bucket). Unlike
HDFC's bucket, `HEAD` works fine here (`200`/`403` as expected) — no need for a ranged GET
probe. In practice this means only a rolling ~4-month window is ever available; older months
are simply gone from this bucket, not hidden behind a different naming scheme (multiple
plausible older-month URLs were probed and all came back `403`).

One combined workbook covers every scheme for that month (SBI/Tata/Nippon-style, not a
PPFAS/Union one-file-per-scheme layout): a `Scheme` sheet lists every `(Abbreviation, Scheme
Name)` pair, and each scheme's holdings live on their own sheet named after that 2-3 letter
abbreviation (e.g. "ELS" = Kotak ELSS Tax Saver Fund). `combined_workbook.find_sheet_code`
resolves the scheme correctly via its existing headerless-fallback path (the `Scheme` sheet's
header row reads "Abbreviations"/"Scheme Name" — "Abbreviations" is not one of the recognised
code-column keywords ("fund/scheme/short code", "short name", "acronym"), so the header-based
path in `find_sheet_code` finds `name_col` but not `code_col` and falls through to the
headerless (code, name) shape-detection fallback, which handles it correctly with no change
needed. Recording it here anyway: a small, optional generalization would be adding
"abbreviation" to `find_sheet_code`'s code-column keyword list for AMCs whose Index/lookup
sheet uses that exact word, e.g.:

    --- a/src/indian_mf_mcp/ingest/amc_adapters/combined_workbook.py
    +++ b/src/indian_mf_mcp/ingest/amc_adapters/combined_workbook.py
    @@ cc = next((idx for idx, t in enumerate(texts) if "fund code" in t or "scheme code" in t
    -                or "short code" in t or "short name" in t or "acronym" in t), None)
    +                or "short code" in t or "short name" in t or "acronym" in t
    +                or "abbreviation" in t), None)

    Not required for Kotak today (the fallback already resolves it), but would make the
    header-based path hit first rather than relying on the fallback, and is lower-risk for
    other AMCs that might phrase it the same way.

**Genuine required generalization — the actual reason this AMC needs adapter-side repair**:
Kotak's per-scheme sheets use a *merged* "Name of Instrument" header cell spanning three
underlying columns (A:C). openpyxl's `values_only` read only returns the header text in the
merge's top-left cell (col 0); the per-row DATA in that same span is NOT merged and instead
uses col 0 as a blank indent, col 1 for a stray debt-instrument coupon number (unrelated to
the name) or a plain space, and col 2 for the actual instrument/section/sub-section name text.
Total/Grand-Total row labels sit even further right, in the column the header calls
"Industry"/"Rating" (col 4), because those rows have no ISIN/industry data to occupy it.
Fed straight to `xlsx_portfolio.py` unmodified, its column-position-from-header-row assumption
(true for every other AMC parsed so far) breaks silently: `cols.name` resolves to col 0, which
is always empty for data rows, so the parser's Franklin-style "ISIN-before-name" fallback
misfires and reads the ISIN string as the instrument name, leaving `isin=None` on every
holding and losing the real names entirely — reconciliation can still land close to 1.0 by
coincidence (value/qty/pct columns are unaffected) but every holding's identity is wrong. This
is a real generalization `xlsx_portfolio.py` would need (scan for the first non-blank string
cell in the row rather than trusting the header's exact column index for the name field), but
per this project's constraints that file is not touched here; the fix instead lives entirely
in this adapter's `_repair_name_column`/`_extract_scheme_sheet`, applied inside `fetch()` before
bytes are handed to the shared parser (nothing requires an adapter to return the network
response byte-for-byte — only that its output be a valid, real, unmodified-content XLSX).
Because the repair also happens inside `fetch()`, this adapter needs no `sheet_resolver`
argument to `ingest_scheme_portfolios` at all: each `fetch()` call already returns a single
extracted, repaired, single-sheet workbook for exactly the scheme in `ref.scheme_hint`, so the
standard "one file per scheme" pipeline (`sheet_resolver=None`) applies unchanged.

Values are already fractional-or-percentage-points depending on scheme (xlsx_portfolio.py's
existing scale detection handles both transparently, same as every other AMC).
"""
from __future__ import annotations

import io
import urllib.parse
from datetime import date

import httpx
import openpyxl

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters import combined_workbook
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

FILES_HOST = "https://vatseelabs-s3.kotakmf.com/FormsDownloads/Portfolios/"
INDEX_SHEET_NAME = "Scheme"


def build_url(as_of: date) -> str:
    """The full S3 key for the month's combined portfolio workbook. Day is not zero-padded;
    both the folder and filename use the as-of month itself (no month-after offset)."""
    folder = f"Consolidated-SEBI-Portfolio-as-on-{as_of.strftime('%B')}-{as_of.day},-{as_of.year}"
    filename = f"ConsolidatedSEBIPortfolio{as_of.strftime('%B')}{as_of.year}.xlsx"
    return FILES_HOST + urllib.parse.quote(folder) + "/" + urllib.parse.quote(filename)


def _month_end(year: int, month: int) -> date:
    import calendar
    return date(year, month, calendar.monthrange(year, month)[1])


def _month_ends_since(since: date, until: date):
    year, month = since.year, since.month
    while True:
        candidate = _month_end(year, month)
        if candidate > until:
            return
        if candidate >= since:
            yield candidate
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _fix_name_column(rows: list[tuple]) -> list[tuple]:
    """Repopulate column 0 (where the shared parser expects the instrument/section/total
    label to live) with whichever column actually holds it for this row. Kotak's merged
    "Name of Instrument" header means real data never lands in col 0: section headers use
    col 0 already (untouched, left alone below), sub-section headers use col 1, holding
    names use col 2, and total/grand-total labels use col 4 (the "Industry"/"Rating" slot,
    since those rows have no such data). Scanning the whole row for the first non-blank
    string handles all four shapes uniformly without hardcoding per-row-type offsets."""
    fixed = []
    for row in rows:
        row = list(row)
        v0 = row[0] if row else None
        if v0 is None or (isinstance(v0, str) and not v0.strip()):
            for c in row:
                if isinstance(c, str) and c.strip():
                    row[0] = c
                    break
        fixed.append(tuple(row))
    return fixed


def _extract_scheme_sheet(raw: bytes, sheet_code: str) -> bytes:
    """Pull just one scheme's sheet out of the combined workbook, repair its name column
    (see _fix_name_column), and return it as a new single-sheet XLSX. All values are the
    genuine, unmodified holdings data — only column *position* is corrected."""
    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    ws = wb[sheet_code]
    rows = list(ws.iter_rows(values_only=True))
    fixed_rows = _fix_name_column(rows)

    out_wb = openpyxl.Workbook()
    out_ws = out_wb.active
    out_ws.title = sheet_code
    for row in fixed_rows:
        out_ws.append(row)
    buf = io.BytesIO()
    out_wb.save(buf)
    return buf.getvalue()


class KotakAdapter:
    amc_id = "amc-kotak"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO or not scheme_hint:
            # fetch() needs a scheme name to pick and repair the right sheet, even though the
            # URL itself is computable without one.
            return []

        headers = {"User-Agent": config.USER_AGENT}
        refs: list[DocumentRef] = []
        owns_client = client is None
        c = client or httpx.Client(timeout=30, follow_redirects=True)
        try:
            for as_of in _month_ends_since(since, date.today()):
                url = build_url(as_of)
                try:
                    resp = c.head(url, headers=headers)
                except httpx.HTTPError:
                    continue  # transient; a later run re-probes this month
                if resp.status_code == 200:
                    refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                             as_of_date=as_of, scheme_hint=scheme_hint))
                # 403 (AccessDenied, ListBucket is denied so absent keys don't 404): no file
                # published for this month, move on.
        finally:
            if owns_client:
                c.close()
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=90)
        resp.raise_for_status()
        raw = resp.content
        if not ref.scheme_hint:
            return raw
        code = combined_workbook.find_sheet_code(raw, ref.scheme_hint,
                                                   index_sheet_name=INDEX_SHEET_NAME)
        if code is None:
            return raw
        return _extract_scheme_sheet(raw, code)
