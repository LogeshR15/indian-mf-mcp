"""AMFI half-yearly stock categorisation (cap list) — fetch, parse, store.

AMFI publishes a stock-categorisation spreadsheet defining which ISINs fall into the
large-cap (top 100), mid-cap (next 150), or small-cap (rest) buckets. This list changes
every six months (January and July effective dates).

Spec §3.6: market-cap allocation requires a point-in-time cap list; using today's list to
reclassify a 2021 portfolio produces false "style drift" signals. We version-stamp every copy
fetched and always look up the version whose effective_date is <= the snapshot date.

Primary URL (portal.amfiindia.com — plain static file, not SPA):
  https://portal.amfiindia.com/spages/acStockCategorization.xlsx

The file has two sheets:
  - Sheet 1: "Large Cap" / "Mid Cap" / "Small Cap" sections with columns ISIN | Company Name
  - Or a flat table with a "Market Cap" column; layout has varied historically.

We handle both layouts via a flexible parser.
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

CAP_LIST_URL = "https://portal.amfiindia.com/spages/acStockCategorization.xlsx"

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

    wb.close()
    return effective_date or _derive_effective_date(), rows


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

def fetch_and_store_caplist(conn: sqlite3.Connection) -> dict:
    """Fetch the current AMFI cap list, store the raw file in the blob store, and upsert
    the isin_market_cap table.

    Returns stats dict with keys: effective_date, isin_count, skipped, blob_path.
    Idempotent: if the sha256 of the downloaded file already exists, the blob is not
    re-stored and the DB is not re-written (detected via the caplist_doc_id in isin_market_cap).
    """
    log.info("Fetching AMFI cap list from %s", CAP_LIST_URL)
    with httpx.Client() as client:
        resp = client.get(
            CAP_LIST_URL,
            headers={"User-Agent": config.USER_AGENT},
            follow_redirects=True,
            timeout=60,
        )
        resp.raise_for_status()
    raw = resp.content
    sha256 = hashlib.sha256(raw).hexdigest()

    # Check if we already have this exact file
    existing = conn.execute(
        "SELECT doc_id FROM document WHERE sha256 = ?", (sha256,)
    ).fetchone()
    if existing:
        return {
            "effective_date": _get_latest_effective_date(conn),
            "isin_count": 0,
            "skipped": True,
            "note": "Cap list unchanged (same sha256); no update needed.",
            "doc_id": existing["doc_id"],
        }

    # Store raw bytes
    _, blob_path = blobstore.put(raw)

    # Parse
    effective_date, entries = _parse_caplist_xlsx(raw)
    log.info("Parsed %d ISIN entries, effective %s", len(entries), effective_date)

    # Store document record
    import uuid
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
        (doc_id, effective_date, CAP_LIST_URL, sha256, str(blob_path), now,
         1.0 if entries else 0.0),
    )

    # Upsert isin_market_cap
    upserted = 0
    for isin, _name, cap_label in entries:
        conn.execute(
            """INSERT OR REPLACE INTO isin_market_cap
               (isin, market_cap, effective_date, caplist_doc_id)
               VALUES (?,?,?,?)""",
            (isin, cap_label, effective_date, doc_id),
        )
        upserted += 1

    conn.commit()
    log.info("Stored %d cap-list entries for effective_date=%s", upserted, effective_date)
    return {
        "effective_date": effective_date,
        "isin_count": upserted,
        "skipped": False,
        "doc_id": doc_id,
        "blob_path": blob_path,
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
