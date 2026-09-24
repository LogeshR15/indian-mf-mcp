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

MONTHLY FACTSHEETS (DocType.FACTSHEET) — ground-truthed live 2026-09-23
---------------------------------------------------------------------
Factsheets do NOT live on `vatseelabs-s3.kotakmf.com` (no factsheet key was ever found there;
public search only surfaces SID/KIM PDFs on that host). They live under
`https://www.kotakmf.com/factsheet/<folder>/`, found via a public web search
(`kotakmf.com/factsheet "Kotak MF Factsheet" pdf`) that surfaced real URLs such as
`/factsheet/may_2026/Kotak%20MF%20Factsheet%20May%202026.pdf` and
`/factsheet/december-2024/Kotak%20MF%20Factsheet%20December%202024.pdf`.

Crucially, although `www.kotakmf.com` is behind Radware (see above), the `/factsheet/...pdf`
paths are not: they are served by an S3 proxy (response header
`x-4front-s3-proxy-key: effiles/<folder>/<file>`, `content-type: application/pdf`, AWS ALB
cookies) and answer this project's honest User-Agent with a clean `200` and the real PDF
every time. The per-month e-factsheet *HTML index* (`/factsheet/<folder>/`, whose
`img/pdf.png` link names the PDF) is Radware-gated: the very first request on 2026-09-23 got a
`200` for `/factsheet/July_2026/`, every later one (any folder) got `302` to
`validate.perfdrive.com` with `server: rdwr`. So the index is never used; the PDF URL is
computed and probed instead (Step 2, route 4 of CONTRIBUTING.md).

ONE combined PDF per month covers every scheme (~190 pages, ~20-28 MB, cover page reads
"(Data as on 31st July 2026) AUGUST 2026 FUND FACT SHEET", page 3 is a scheme→page index), so
`list_documents` returns the same URL whatever `scheme_hint` is (the caller scopes pages to
the scheme). Per-scheme one-pager PDFs also exist
(`/factsheet/<folder>/kotak/Download_pdf/<SCHEME NAME UPPERCASE>.pdf`) but are not used.

URL convention — irregular, hence probing rather than one formula:

    https://www.kotakmf.com/factsheet/<folder>/Kotak MF Factsheet <Month> <YYYY>.pdf

  - `<folder>` is always the DATA month (the month-end the factsheet describes), but its
    spelling is hand-typed each month: seen live `January_2025`, `august_2025`,
    `october_2025`, `December-2025`, `feb_2026`, `March2026`, `may_2026`, `July_2026`,
    `august-2024` / `december-2024` (all of 2024 is `<month>-<yyyy>` lower-case),
    `February-2022`. The S3 key is case-sensitive, so every spelling must be tried.
  - The filename month is USUALLY the data month, but not always: the July 2026 data-month
    PDF is `July_2026/Kotak MF Factsheet August 2026.pdf` (named for its publication month),
    while `August_2026/Kotak MF Factsheet August 2026.pdf` is August data. Both same-month and
    next-month filenames are therefore candidates; the folder alone fixes `as_of_date`.
    Folder-month == data-month was verified against page 1's "Data as on ..." for Feb, May,
    Jun, Jul and Aug 2026.
  - `HEAD` is useless here: it returns `200 text/html` for ANY path, existing or not. A
    missing key only shows up on `GET` (`404`, `application/xml`, S3 NoSuchKey body). The
    proxy ignores `Range`, so probes stream the GET and close after the first 8 bytes,
    accepting only a `200` whose body starts with `%PDF` (a Radware `302` is never followed
    and never counts as a hit).

History reach (probed 2026-09-23): every month Jan 2024 – Aug 2026 resolves EXCEPT July 2024
and a handful of candidates never matched for most of 2022-2023 (only Jan/Feb 2022 and
Dec 2023 hit) — those months presumably use a different filename; they are simply absent
from listings rather than guessed. The factsheet for month M appears in the first ~10 days
of month M+1 (August 2026's PDF was modified 2026-09-09).
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


FACTSHEET_HOST = "https://www.kotakmf.com/factsheet/"


def factsheet_candidate_urls(as_of: date) -> list[str]:
    """Every plausible URL for the combined factsheet describing `as_of`'s month, most
    likely first (see module docstring: folder spelling and filename month vary)."""
    import calendar
    full = calendar.month_name[as_of.month]
    abbr = calendar.month_abbr[as_of.month]
    nxt = date(as_of.year + (as_of.month == 12), as_of.month % 12 + 1, 1)
    y = as_of.year
    folders: list[str] = []
    for tok in (full, full.lower(), abbr.lower(), abbr):
        for sep in ("_", "-", ""):
            name = f"{tok}{sep}{y}"
            if name not in folders:
                folders.append(name)
    files = [f"Kotak MF Factsheet {full} {y}.pdf",
             f"Kotak MF Factsheet {calendar.month_name[nxt.month]} {nxt.year}.pdf"]
    return [FACTSHEET_HOST + urllib.parse.quote(f) + "/" + urllib.parse.quote(fn)
            for f in folders for fn in files]


def _is_pdf(c: httpx.Client, url: str, headers: dict) -> bool:
    """True iff `url` GETs a 200 whose body starts with %PDF. HEAD can't be used (200 for
    any path) and Range is ignored, so stream and stop after the first bytes. Redirects
    (Radware's 302) are not followed and never count."""
    try:
        with c.stream("GET", url, headers=headers, follow_redirects=False) as resp:
            if resp.status_code != 200:
                return False
            for chunk in resp.iter_bytes(8):
                return chunk.startswith(b"%PDF")
            return False
    except httpx.HTTPError:
        return False


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
    # Factsheets: one PDF per month for every scheme.
    factsheet_scope = "combined"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type == DocType.FACTSHEET:
            return self._list_factsheets(since, scheme_hint, client)
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

    def _list_factsheets(self, since: date, scheme_hint: str | None,
                         client: httpx.Client | None) -> list[DocumentRef]:
        """One combined PDF per month for all schemes: same URL whatever scheme_hint is."""
        headers = {"User-Agent": config.USER_AGENT}
        refs: list[DocumentRef] = []
        owns_client = client is None
        c = client or httpx.Client(timeout=30)
        try:
            for as_of in _month_ends_since(since, date.today()):
                url = next((u for u in factsheet_candidate_urls(as_of)
                            if _is_pdf(c, u, headers)), None)
                if url is not None:
                    refs.append(DocumentRef(url=url, doc_type=DocType.FACTSHEET,
                                             as_of_date=as_of, scheme_hint=scheme_hint))
        finally:
            if owns_client:
                c.close()
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        if ref.doc_type == DocType.FACTSHEET:
            get = client.get if client is not None else httpx.get
            resp = get(ref.url, headers=headers, follow_redirects=False, timeout=180)
            resp.raise_for_status()
            if not resp.content.startswith(b"%PDF"):
                raise ValueError(f"Kotak factsheet URL did not return a PDF: {ref.url}")
            return resp.content
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
