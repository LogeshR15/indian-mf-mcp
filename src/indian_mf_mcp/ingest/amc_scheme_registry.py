"""Scheme-to-adapter-hint registry for bulk backfill (spec §8.2, Phase 4).

The `scheme_adapter_hint` table maps our internal scheme_id to the hint string the AMC
adapter's list_documents() recognises (typically the canonical scheme name as it appears
in the AMC's own filenames or listing pages).

Populated:
  1. Automatically during navall ingest — when we see a scheme for the first time, we
     store its normalised name as the adapter hint (good enough for ~80% of adapters).
     Rows are keyed by the AMFI-derived `scheme.amc_id`; get_schemes_for_amc translates a
     CLI adapter key onto that namespace via ingest/amc_identity.py.
  2. Explicitly via 'mf-mcp register-scheme-hint' for schemes where the AMC uses a
     different naming convention.

Consumed:
  'mf-mcp backfill --amc <amc>' — iterates all schemes for the AMC and calls
  ingest_scheme_portfolios / ingest_factsheet for each, using the stored hint.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from indian_mf_mcp.ingest import amc_identity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_scheme_hint(
    conn: sqlite3.Connection,
    scheme_id: str,
    amc_id: str,
    adapter_hint: str,
) -> None:
    """Store or update the adapter hint for a scheme.

    Called during navall ingest when a new scheme is encountered, and by the
    'register-scheme-hint' CLI command for manual overrides.
    """
    conn.execute(
        """INSERT OR REPLACE INTO scheme_adapter_hint
           (scheme_id, amc_id, adapter_hint, updated_at)
           VALUES (?,?,?,?)""",
        (scheme_id, amc_id, adapter_hint, _now()),
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_schemes_for_amc(conn: sqlite3.Connection, amc: str) -> list[dict]:
    """Return all schemes with a registered adapter hint for the given AMC.

    `amc` is an adapter key as the CLI takes it ("ppfas", "bank-of-india"); it is
    translated to the AMFI-derived amc_id(s) the store actually files schemes under.
    Passing a raw amc_id works too, so existing callers and tests keep working.

    Returns list of dicts: [{scheme_id, name, adapter_hint, category, sub_category, active}]
    Sorted by scheme name for deterministic output.
    """
    # `amc` itself stays in the candidate list: rows written by `register-scheme-hint
    # --amc ppfas` before adapter-key translation existed are keyed by the bare adapter
    # key, and must keep resolving rather than silently disappearing on upgrade.
    amc_ids = [amc, *amc_identity.amc_ids_for_adapter(amc)] if amc in amc_identity.AMFI_AMC_NAME else [amc]
    placeholders = ",".join("?" * len(amc_ids))
    rows = conn.execute(
        f"""SELECT sah.scheme_id, sah.adapter_hint,
                   s.name, s.category, s.sub_category, s.active
            FROM scheme_adapter_hint sah
            JOIN scheme s ON s.scheme_id = sah.scheme_id
            WHERE sah.amc_id IN ({placeholders})
            ORDER BY s.name""",
        amc_ids,
    ).fetchall()
    return [dict(r) for r in rows]


def get_hint_for_scheme(
    conn: sqlite3.Connection, scheme_id: str, amc_id: str
) -> str | None:
    """Return the adapter hint for a specific scheme, or None if not registered."""
    row = conn.execute(
        "SELECT adapter_hint FROM scheme_adapter_hint WHERE scheme_id = ? AND amc_id = ?",
        (scheme_id, amc_id),
    ).fetchone()
    return row["adapter_hint"] if row else None


def list_amc_ids_with_hints(conn: sqlite3.Connection) -> list[str]:
    """Return distinct amc_ids that have at least one registered scheme hint."""
    rows = conn.execute(
        "SELECT DISTINCT amc_id FROM scheme_adapter_hint ORDER BY amc_id"
    ).fetchall()
    return [r["amc_id"] for r in rows]


# ---------------------------------------------------------------------------
# Auto-population from navall ingest
# ---------------------------------------------------------------------------

def auto_register_from_navall(conn: sqlite3.Connection) -> int:
    """Register adapter hints for all schemes that don't yet have one.

    Uses the scheme's canonical name as the adapter hint — works for most adapters
    where the scheme name in the AMC's file matches the AMFI canonical name.
    Skips schemes that already have a registered hint (preserves manual overrides).

    Returns the number of newly registered hints.
    """
    # Find scheme + amc pairs that have no hint yet
    rows = conn.execute(
        """SELECT s.scheme_id, s.name, s.amc_id
           FROM scheme s
           LEFT JOIN scheme_adapter_hint sah
               ON sah.scheme_id = s.scheme_id AND sah.amc_id = s.amc_id
           WHERE sah.scheme_id IS NULL
             AND s.amc_id IS NOT NULL
             AND s.active = 1""",
    ).fetchall()

    count = 0
    now = _now()
    for row in rows:
        conn.execute(
            """INSERT OR IGNORE INTO scheme_adapter_hint
               (scheme_id, amc_id, adapter_hint, updated_at)
               VALUES (?,?,?,?)""",
            (row["scheme_id"], row["amc_id"], row["name"], now),
        )
        count += 1
    return count
