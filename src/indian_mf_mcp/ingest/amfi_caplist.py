"""AMFI half-yearly stock categorisation (cap list) — fetch, parse, store.

AMFI publishes a stock-categorisation spreadsheet defining which ISINs fall into the
large-cap (top 100), mid-cap (next 150), or small-cap (rest) buckets. This list changes
every six months (January and July effective dates).

Spec §3.6: market-cap allocation requires a point-in-time cap list; using today's list to
reclassify a 2021 portfolio produces false "style drift" signals. We version-stamp every copy
fetched and always look up the version whose effective_date is <= the snapshot date.

Source (rediscovered after AMFI's 2026 site rebuild): the listing page

  https://www.amfiindia.com/otherdata/categorisation-of-stocks

links every half-yearly spreadsheet AMFI has published, not just the current one. The old
hardcoded `spages/acStockCategorization.xlsx` began returning 404 in that rebuild; note the
page uses the British spelling "categorisation", which is why the obvious URL guesses miss.

Discovering from the listing page is strictly better than a single pinned URL: spec §3.6
wants point-in-time classification, and one URL could only ever give us *today's* list, so
every portfolio older than the first fetch got classified against the wrong list or not at
all. Ingesting the whole archive (currently 30 Jun 2022 onward) is what makes
get_market_cap()'s `effective_date <= as_of_date` lookup mean something.

File layout (stable across the archive):
  row 1   title, e.g. "Average Market Capitalization ... six months ended 30 June 2026"
  row 2   header: Sr. No. | Company name | ISIN | BSE Symbol | BSE 6 month Avg ... | NSE Symbol
  rows 3+ one company per row, DESCENDING by average market cap

There is no cap-label column: AMFI publishes the ranking, and SEBI's categorisation rule
(circular SEBI/HO/IMD/DF3/CIR/P/2017/114) defines the buckets by rank — 1-100 large cap,
101-250 mid cap, 251+ small cap. We apply that rule to the ranked rows rather than guessing
from company names. The older section-header and flat-table parsers are kept as fallbacks
for any file that does carry explicit labels.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import sqlite3
from datetime import date, datetime
from typing import Iterator

import httpx
import openpyxl

from indian_mf_mcp import config
from indian_mf_mcp.store import blobstore

log = logging.getLogger(__name__)

# Listing page that indexes every published half-yearly spreadsheet.
CAP_LIST_INDEX_URL = "https://www.amfiindia.com/otherdata/categorisation-of-stocks"

# Retained only so existing `document.source_url` rows remain interpretable; nothing
# fetches it any more (it 404s since AMFI's site rebuild).
CAP_LIST_URL = "https://portal.amfiindia.com/spages/acStockCategorization.xlsx"

# SEBI categorisation by rank on AMFI's average-market-cap ranking.
_LARGE_CAP_MAX_RANK = 100
_MID_CAP_MAX_RANK = 250

# Filenames are inconsistent across vintages — "AverageMarketCapitalization30Jun2026.xlsx",
# the long "...oflistedcompaniesduringthesixmonthsended31Dec2022.xlsx", and the Strapi-era
# "Average_Market_Capitalization_30_Jun2024_<hash>.xlsx" — so allow separators between the
# words. Missing one file is not cosmetic: it leaves a 12-month hole in which portfolios
# get classified against a stale list.
# The trailing \s* is load-bearing: at least one href on the page is written with a space
# before the closing quote ("...Jun2024_2a1ab4c1d8.xlsx "), and requiring .xlsx" dropped
# that entire half-year.
_CAP_FILE_RE = re.compile(
    r'href="([^"]*Average[_\s-]*Market[_\s-]*Capitali[sz]ation[^"]*\.xlsx)\s*"', re.IGNORECASE
)

# "...ended 30 June 2026" / "...ended31Dec2025.xlsx" / "..._30_Jun2024_<hash>.xlsx" — the
# effective date lives in both the title row and the filename; the filename is the more
# reliable of the two.
_FILENAME_DATE_RE = re.compile(r"(\d{1,2})[_\s-]*([A-Za-z]{3,9})[_\s-]*(\d{4})")

# AMFI publishes updated lists in Jan and Jul — effective from the 1st of those months.
# If we can't parse the date from the file itself, we derive it from the current date.
_EFFECTIVE_DATE_RE = re.compile(
    r"(?:effective|as\s+of|from)[:\s]+(\d{1,2}[- /]\w+[- /]\d{2,4})", re.IGNORECASE
)

_SECTION_RE = re.compile(
    r"^(large[\s\-]+cap|mid[\s\-]+cap|small[\s\-]+cap)", re.IGNORECASE
)
_ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}$")

# Map section header text → canonical cap label
_CAP_LABEL: dict[str, str] = {}
for _pat, _label in [
    ("large", "large"),
    ("mid", "mid"),
    ("small", "small"),
]:
    _CAP_LABEL[_pat] = _label


def _derive_effective_date() -> str:
    """Return Jan-1 or Jul-1 of the current half-year as a fallback effective date."""
    today = date.today()
    if today.month >= 7:
        return date(today.year, 7, 1).isoformat()
    return date(today.year, 1, 1).isoformat()


def _parse_caplist_xlsx(raw: bytes) -> tuple[str, list[tuple[str, str, str]]]:
    """Parse AMFI cap-list XLSX.

    Returns:
        (effective_date_iso, [(isin, company_name, cap_label), ...])
    """
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    rows: list[tuple[str, str, str]] = []
    effective_date: str | None = None

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        current_cap: str | None = None
        # Try to detect a "Market Cap" column layout (flat table)
        header_row: list[str] = []
        isin_col = cap_col = name_col = -1
        flat_mode = False

        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if not any(cells):
                continue

            # Detect effective date in cell text
            if effective_date is None:
                for cell in cells:
                    m = _EFFECTIVE_DATE_RE.search(cell)
                    if m:
                        try:
                            from dateutil import parser as dtp
                            effective_date = dtp.parse(m.group(1), dayfirst=True).date().isoformat()
                        except Exception:
                            pass
                        break

            # Detect header row for flat layout
            if isin_col == -1 and "isin" in [c.lower() for c in cells]:
                header_row = [c.lower() for c in cells]
                for i, h in enumerate(header_row):
                    if "isin" in h:
                        isin_col = i
                    if "market cap" in h or "marketcap" in h or "category" in h:
                        cap_col = i
                    if "company" in h or "name" in h or "instrument" in h:
                        name_col = i
                if isin_col != -1 and cap_col != -1:
                    flat_mode = True
                continue

            if flat_mode:
                # Flat layout: each row is isin | name | market_cap
                if isin_col < len(cells) and _ISIN_RE.match(cells[isin_col]):
                    isin = cells[isin_col]
                    name = cells[name_col] if name_col != -1 and name_col < len(cells) else ""
                    cap_raw = cells[cap_col] if cap_col < len(cells) else ""
                    cap_label = _classify_cap_raw(cap_raw)
                    if cap_label and isin:
                        rows.append((isin, name, cap_label))
                continue

            # Section-header layout
            joined = " ".join(c for c in cells if c)
            m = _SECTION_RE.search(joined)
            if m:
                current_cap = _classify_cap_raw(m.group(1))
                continue

            if current_cap:
                # Find the ISIN in this row
                for c in cells:
                    if _ISIN_RE.match(c):
                        name = next((x for x in cells if x and x != c and not _ISIN_RE.match(x)), "")
                        rows.append((c, name, current_cap))
                        break

    if not rows:
        # No explicit cap labels anywhere — AMFI's current (and archival) format. Classify
        # by rank instead, which is what SEBI's rule actually specifies.
        for sheet_name in wb.sheetnames:
            rows = _parse_ranked_caplist(wb[sheet_name])
            if rows:
                break

    if effective_date is None:
        # The title row carries the period end ("... six months ended 30 June 2026").
        for sheet_name in wb.sheetnames:
            for row in wb[sheet_name].iter_rows(min_row=1, max_row=3, values_only=True):
                for cell in row:
                    if cell and "ended" in str(cell).lower():
                        effective_date = _effective_date_from_name(str(cell))
                        break
                if effective_date:
                    break
            if effective_date:
                break

    wb.close()
    return effective_date or _derive_effective_date(), rows


def _cap_for_rank(rank: int) -> str:
    """SEBI's rank buckets: 1-100 large, 101-250 mid, 251+ small."""
    if rank <= _LARGE_CAP_MAX_RANK:
        return "large"
    if rank <= _MID_CAP_MAX_RANK:
        return "mid"
    return "small"


def _parse_ranked_caplist(ws) -> list[tuple[str, str, str]]:
    """Parse AMFI's ranked layout: one company per row, descending by average market cap.

    Rank comes from row order, not the "Sr. No." column — that column is blank or repeated
    in some vintages, and a wrong rank silently moves a stock between cap buckets.
    """
    entries: list[tuple[str, str, str]] = []
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip() if c is not None else "" for c in row]
        isin = next((c for c in cells if _ISIN_RE.match(c)), None)
        if not isin:
            continue
        name = ""
        for c in cells:
            if c and c != isin and not _ISIN_RE.match(c) and not c.replace(".", "").isdigit():
                name = c
                break
        entries.append((isin, name, _cap_for_rank(len(entries) + 1)))
    return entries


def _effective_date_from_name(text: str) -> str | None:
    """Pull the period-end date out of a filename or title row."""
    m = _FILENAME_DATE_RE.search(text)
    if not m:
        return None
    day, month, year = m.groups()
    try:
        from dateutil import parser as dtp
        return dtp.parse(f"{day} {month} {year}", dayfirst=True).date().isoformat()
    except Exception:
        return None


def discover_caplist_urls(client: httpx.Client | None = None) -> list[tuple[str, str | None]]:
    """Scrape the listing page for every published cap-list file.

    Returns [(absolute_url, effective_date_iso_or_None), ...], newest first.
    """
    headers = {"User-Agent": config.USER_AGENT}
    if client is None:
        with httpx.Client() as c:
            resp = c.get(CAP_LIST_INDEX_URL, headers=headers, follow_redirects=True, timeout=60)
    else:
        resp = client.get(CAP_LIST_INDEX_URL, headers=headers, follow_redirects=True, timeout=60)
    resp.raise_for_status()

    seen: dict[str, str | None] = {}
    for href in _CAP_FILE_RE.findall(resp.text):
        href = href.strip()
        url = href if href.startswith("http") else "https://www.amfiindia.com" + (
            href if href.startswith("/") else "/" + href
        )
        seen.setdefault(url, _effective_date_from_name(url))
    # Newest first; undated entries last so a dated file always wins a same-period tie.
    return sorted(seen.items(), key=lambda kv: (kv[1] is None, kv[1] or ""), reverse=True)


def _classify_cap_raw(text: str) -> str | None:
    """Map raw cap label text to 'large' | 'mid' | 'small' | None."""
    t = text.lower()
    if "large" in t:
        return "large"
    if "mid" in t:
        return "mid"
    if "small" in t:
        return "small"
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _ingest_one(
    conn: sqlite3.Connection,
    url: str,
    date_hint: str | None,
    client: httpx.Client | None = None,
) -> dict:
    """Fetch, archive and load a single cap-list file. Idempotent on sha256."""
    headers = {"User-Agent": config.USER_AGENT}
    if client is None:
        with httpx.Client() as c:
            resp = c.get(url, headers=headers, follow_redirects=True, timeout=120)
    else:
        resp = client.get(url, headers=headers, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    raw = resp.content
    sha256 = hashlib.sha256(raw).hexdigest()

    existing = conn.execute(
        "SELECT doc_id FROM document WHERE sha256 = ?", (sha256,)
    ).fetchone()
    if existing:
        return {"url": url, "skipped": True, "isin_count": 0, "doc_id": existing["doc_id"]}

    _, blob_path = blobstore.put(raw)
    parsed_date, entries = _parse_caplist_xlsx(raw)
    # The filename date is authoritative: a title row can be copy-pasted from the previous
    # half-year (AMFI has shipped that), and a wrong effective_date silently misclassifies
    # every portfolio in the affected window.
    effective_date = date_hint or parsed_date

    from datetime import timezone
    doc_id = f"caplist_{effective_date}_{sha256[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO document
           (doc_id, scheme_id, amc_id, doc_type, doc_date, source_url, sha256,
            content_type, blob_path, retrieved_at, page_count, parse_status, parse_confidence)
           VALUES (?,NULL,NULL,'caplist',?,?,?,
                   'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                   ?,?,NULL,'parsed',?)""",
        (doc_id, effective_date, url, sha256, str(blob_path), now, 1.0 if entries else 0.0),
    )

    conn.executemany(
        """INSERT OR REPLACE INTO isin_market_cap
           (isin, market_cap, effective_date, caplist_doc_id) VALUES (?,?,?,?)""",
        [(isin, cap, effective_date, doc_id) for isin, _name, cap in entries],
    )
    log.info("Stored %d cap-list entries for effective_date=%s", len(entries), effective_date)
    return {
        "url": url,
        "skipped": False,
        "effective_date": effective_date,
        "isin_count": len(entries),
        "doc_id": doc_id,
        "blob_path": str(blob_path),
    }


def fetch_and_store_caplist(conn: sqlite3.Connection, latest_only: bool = False) -> dict:
    """Discover every published AMFI cap list and load the ones we don't already have.

    Loading the full archive rather than only the current file is what makes
    get_market_cap() point-in-time correct — with a single version, every portfolio
    predating the first fetch was classified against a list that did not yet exist.

    Idempotent: files are keyed by sha256, so a rerun re-downloads but re-parses and
    re-writes nothing. Returns aggregate stats; `versions` details each file.
    """
    discovered = discover_caplist_urls()
    if not discovered:
        raise RuntimeError(
            f"No cap-list files found at {CAP_LIST_INDEX_URL} — AMFI has likely changed "
            "the page layout again. The link pattern is in _CAP_FILE_RE."
        )
    if latest_only:
        discovered = discovered[:1]

    versions: list[dict] = []
    errors: list[dict] = []
    with httpx.Client() as client:
        for url, date_hint in discovered:
            try:
                versions.append(_ingest_one(conn, url, date_hint, client=client))
            except Exception as exc:  # noqa: BLE001
                # One bad half-year must not lose the other eight; the gap is reported.
                errors.append({"url": url, "error": str(exc)})
                log.warning("Cap list %s failed: %s", url, exc)
    conn.commit()

    loaded = [v for v in versions if not v["skipped"]]
    return {
        "effective_date": _get_latest_effective_date(conn),
        "isin_count": sum(v["isin_count"] for v in loaded),
        "versions_discovered": len(discovered),
        "versions_loaded": len(loaded),
        "versions_skipped": len(versions) - len(loaded),
        "skipped": not loaded and not errors,
        "versions": versions,
        "errors": errors,
    }


def get_market_cap(conn: sqlite3.Connection, isin: str, as_of_date: str) -> str | None:
    """Return 'large' | 'mid' | 'small' | None for an ISIN as of a given date.

    Uses the cap-list version whose effective_date is <= as_of_date (point-in-time safe).
    Never uses a future cap list to reclassify a past portfolio.
    """
    row = conn.execute(
        """SELECT market_cap FROM isin_market_cap
           WHERE isin = ? AND effective_date <= ?
           ORDER BY effective_date DESC LIMIT 1""",
        (isin, as_of_date),
    ).fetchone()
    return row["market_cap"] if row else None


def get_latest_effective_date(conn: sqlite3.Connection) -> str | None:
    """Return the most recent effective_date in isin_market_cap, or None if empty."""
    return _get_latest_effective_date(conn)


def _get_latest_effective_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT MAX(effective_date) AS ed FROM isin_market_cap"
    ).fetchone()
    return row["ed"] if row else None


def compute_market_cap_allocation(
    conn: sqlite3.Connection,
    holdings_rows: list,
    as_of_date: str,
) -> dict:
    """Aggregate market-cap allocation for a list of holding rows.

    Only considers equity, foreign, and REIT rows (same population as concentration stats).
    Returns:
        {large_pct, mid_pct, small_pct, unclassified_pct, cap_list_date, n_classified,
         caveat}
    """
    eff_date = _get_latest_effective_date(conn)
    if eff_date is None or eff_date > as_of_date:
        # No cap list, or only future cap lists available
        return {
            "large_pct": None, "mid_pct": None, "small_pct": None,
            "unclassified_pct": None, "cap_list_date": None,
            "n_classified": 0,
            "caveat": (
                "No AMFI cap list available in the store. "
                "Run 'mf-mcp update-caplist' to download it."
            ),
        }

    large = mid = small = unclassified = 0.0
    n_classified = 0
    cap_list_date = None

    for r in holdings_rows:
        ac = r["asset_class"] or "other"
        if ac not in ("equity", "foreign", "reit"):
            continue
        pct = r["pct_nav"] or 0.0
        isin = r["isin"]
        if not isin:
            unclassified += pct
            continue

        # Find the cap list whose effective_date <= as_of_date (point-in-time)
        row = conn.execute(
            """SELECT market_cap, effective_date FROM isin_market_cap
               WHERE isin = ? AND effective_date <= ?
               ORDER BY effective_date DESC LIMIT 1""",
            (isin, as_of_date),
        ).fetchone()

        if row is None:
            unclassified += pct
            continue

        cap_label = row["market_cap"]
        if cap_list_date is None or row["effective_date"] > cap_list_date:
            cap_list_date = row["effective_date"]
        n_classified += 1
        if cap_label == "large":
            large += pct
        elif cap_label == "mid":
            mid += pct
        elif cap_label == "small":
            small += pct
        else:
            unclassified += pct

    return {
        "large_pct": round(large, 2),
        "mid_pct": round(mid, 2),
        "small_pct": round(small, 2),
        "unclassified_pct": round(unclassified, 2),
        "cap_list_date": cap_list_date,
        "n_classified": n_classified,
        "caveat": (
            f"Point-in-time cap list ({cap_list_date}) used. "
            "ISINs not in the AMFI list (foreign equities, REITs with no ISIN, unlisted) "
            "are reported as unclassified. "
            "AMFI updates the list every six months (Jan and Jul)."
        ) if n_classified > 0 else (
            "No ISINs from this portfolio were found in the AMFI cap list. "
            "This may indicate the cap list needs to be refreshed via 'mf-mcp update-caplist'."
        ),
    }
