"""Phase 3: Persistence helpers for Manager, ManagerAssignment, ChangeEvent, TERHistory."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Manager identity
# ---------------------------------------------------------------------------

def _manager_id(name_normalised: str) -> str:
    return "mgr-" + hashlib.sha1(name_normalised.lower().encode()).hexdigest()[:16]


def upsert_manager(conn: sqlite3.Connection, name_normalised: str,
                   alias: str | None = None) -> str:
    """Insert or update a manager row. Returns manager_id. Alias is merged into the
    JSON aliases list — never overwrites, only adds new spellings."""
    mgr_id = _manager_id(name_normalised)
    row = conn.execute("SELECT * FROM manager WHERE manager_id = ?", (mgr_id,)).fetchone()
    if row is None:
        aliases = [alias] if alias and alias != name_normalised else []
        conn.execute(
            "INSERT INTO manager (manager_id, name_normalised, aliases_json) VALUES (?, ?, ?)",
            (mgr_id, name_normalised, json.dumps(aliases)),
        )
    elif alias and alias != name_normalised:
        existing = json.loads(row["aliases_json"] or "[]")
        if alias not in existing:
            existing.append(alias)
            conn.execute(
                "UPDATE manager SET aliases_json = ? WHERE manager_id = ?",
                (json.dumps(existing), mgr_id),
            )
    return mgr_id


def get_manager_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """Look up a manager by normalised name or any alias."""
    row = conn.execute(
        "SELECT * FROM manager WHERE name_normalised = ?", (name,)
    ).fetchone()
    if row:
        return row
    # search aliases
    all_rows = conn.execute("SELECT * FROM manager").fetchall()
    for r in all_rows:
        aliases = json.loads(r["aliases_json"] or "[]")
        if name in aliases:
            return r
    return None


def get_managers_for_scheme(conn: sqlite3.Connection, scheme_id: str) -> list[sqlite3.Row]:
    """Return current manager assignments (to_date IS NULL) for a scheme, with manager name."""
    return conn.execute(
        """SELECT ma.*, m.name_normalised, m.aliases_json
           FROM manager_assignment ma
           JOIN manager m ON m.manager_id = ma.manager_id
           WHERE ma.scheme_id = ?
           ORDER BY ma.from_date DESC""",
        (scheme_id,),
    ).fetchall()


def get_current_managers_for_scheme(conn: sqlite3.Connection, scheme_id: str) -> list[sqlite3.Row]:
    """Only currently-active assignments (to_date IS NULL)."""
    return conn.execute(
        """SELECT ma.*, m.name_normalised, m.aliases_json
           FROM manager_assignment ma
           JOIN manager m ON m.manager_id = ma.manager_id
           WHERE ma.scheme_id = ? AND ma.to_date IS NULL
           ORDER BY ma.from_date DESC""",
        (scheme_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Manager assignment
# ---------------------------------------------------------------------------

def _assignment_id(scheme_id: str, manager_id: str, from_date: str) -> str:
    key = f"{scheme_id}::{manager_id}::{from_date}"
    return "asgn-" + hashlib.sha1(key.encode()).hexdigest()[:16]


def upsert_manager_assignment(
    conn: sqlite3.Connection,
    scheme_id: str,
    manager_id: str,
    from_date: str,
    to_date: str | None = None,
    evidence_doc_id: str | None = None,
    confidence: str = "observed",
    notes: str | None = None,
) -> str:
    """Upsert a manager assignment. Returns assignment_id.

    If an assignment for this (scheme_id, manager_id, from_date) already exists, only
    to_date is updated — earlier from_date or evidence is never silently overwritten
    (spec §4 ChangeEvent.confidence = 'addendum' > 'observed').
    """
    asgn_id = _assignment_id(scheme_id, manager_id, from_date)
    conn.execute(
        """INSERT INTO manager_assignment
           (assignment_id, scheme_id, manager_id, from_date, to_date,
            evidence_doc_id, confidence, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(assignment_id) DO UPDATE SET
             to_date=COALESCE(excluded.to_date, to_date),
             evidence_doc_id=COALESCE(excluded.evidence_doc_id, evidence_doc_id),
             notes=COALESCE(excluded.notes, notes)""",
        (asgn_id, scheme_id, manager_id, from_date, to_date,
         evidence_doc_id, confidence, notes),
    )
    return asgn_id


def close_assignment(conn: sqlite3.Connection, scheme_id: str, manager_id: str,
                     to_date: str) -> None:
    """Mark a previously-open assignment as ended (to_date set)."""
    conn.execute(
        """UPDATE manager_assignment SET to_date = ?
           WHERE scheme_id = ? AND manager_id = ? AND to_date IS NULL""",
        (to_date, scheme_id, manager_id),
    )


# ---------------------------------------------------------------------------
# ChangeEvent
# ---------------------------------------------------------------------------

def _event_id(scheme_id: str, event_type: str, detected_date: str, suffix: str = "") -> str:
    key = f"{scheme_id}::{event_type}::{detected_date}::{suffix}"
    return "evt-" + hashlib.sha1(key.encode()).hexdigest()[:16]


def insert_change_event(
    conn: sqlite3.Connection,
    scheme_id: str,
    event_type: str,
    detected_date: str,
    effective_date: str | None = None,
    detected_from: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    evidence_doc_id: str | None = None,
    confidence: str = "observed",
) -> str:
    """Insert a ChangeEvent. Returns event_id. Idempotent on content hash."""
    suffix = (json.dumps(before or {}) + json.dumps(after or {}))[:64]
    evt_id = _event_id(scheme_id, event_type, detected_date, suffix)
    conn.execute(
        """INSERT INTO change_event
           (event_id, scheme_id, event_type, effective_date, detected_date,
            detected_from, before_json, after_json, evidence_doc_id, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(event_id) DO NOTHING""",
        (evt_id, scheme_id, event_type, effective_date, detected_date,
         detected_from, json.dumps(before) if before else None,
         json.dumps(after) if after else None,
         evidence_doc_id, confidence),
    )
    return evt_id


def get_change_events(
    conn: sqlite3.Connection,
    scheme_id: str,
    event_type: str | None = None,
    since: str | None = None,
) -> list[sqlite3.Row]:
    q = "SELECT * FROM change_event WHERE scheme_id = ?"
    params: list = [scheme_id]
    if event_type:
        q += " AND event_type = ?"
        params.append(event_type)
    if since:
        q += " AND detected_date >= ?"
        params.append(since)
    q += " ORDER BY detected_date DESC"
    return conn.execute(q, params).fetchall()


# ---------------------------------------------------------------------------
# TER history
# ---------------------------------------------------------------------------

def upsert_ter(
    conn: sqlite3.Connection,
    plan_id: str,
    as_of_date: str,
    ter_pct: float,
    source: str = "amc_disclosure",
    source_doc_id: str | None = None,
) -> None:
    """Upsert a TER data point for a plan. ter_pct is in % p.a. (e.g. 0.63 = 0.63%)."""
    retrieved_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO ter_history
           (plan_id, as_of_date, ter_pct, source, source_doc_id, retrieved_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(plan_id, as_of_date) DO UPDATE SET
             ter_pct=excluded.ter_pct,
             source=excluded.source,
             source_doc_id=excluded.source_doc_id,
             retrieved_at=excluded.retrieved_at""",
        (plan_id, as_of_date, ter_pct, source, source_doc_id, retrieved_at),
    )


def get_latest_ter(conn: sqlite3.Connection, plan_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM ter_history WHERE plan_id = ? ORDER BY as_of_date DESC LIMIT 1",
        (plan_id,),
    ).fetchone()


def get_ter_history(conn: sqlite3.Connection, plan_id: str,
                    since: str | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM ter_history WHERE plan_id = ?"
    params: list = [plan_id]
    if since:
        q += " AND as_of_date >= ?"
        params.append(since)
    q += " ORDER BY as_of_date ASC"
    return conn.execute(q, params).fetchall()
