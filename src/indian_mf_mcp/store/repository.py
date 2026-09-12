"""Upsert / query helpers over the schema. Thin wrappers, no business logic."""
from __future__ import annotations

import sqlite3
from datetime import date


def upsert_amc(conn: sqlite3.Connection, amc_id: str, name: str) -> None:
    conn.execute(
        """INSERT INTO amc (amc_id, name) VALUES (?, ?)
           ON CONFLICT(amc_id) DO UPDATE SET name=excluded.name""",
        (amc_id, name),
    )


def upsert_scheme(
    conn: sqlite3.Connection,
    scheme_id: str,
    amc_id: str,
    name: str,
    scheme_type: str,
    category: str,
    sub_category: str,
    seen_date: str,
) -> None:
    conn.execute(
        """INSERT INTO scheme (scheme_id, amc_id, name, scheme_type, category, sub_category,
                                active, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
           ON CONFLICT(scheme_id) DO UPDATE SET
             name=excluded.name, scheme_type=excluded.scheme_type,
             category=excluded.category, sub_category=excluded.sub_category,
             active=1, last_seen=excluded.last_seen""",
        (scheme_id, amc_id, name, scheme_type, category, sub_category, seen_date, seen_date),
    )


def insert_taxonomy_history(
    conn: sqlite3.Connection,
    scheme_id: str,
    as_of_date: str,
    scheme_type: str,
    category: str,
    sub_category: str,
    raw_header_string: str,
) -> None:
    conn.execute(
        """INSERT INTO scheme_taxonomy_history
           (scheme_id, as_of_date, scheme_type, category, sub_category, raw_header_string)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(scheme_id, as_of_date) DO NOTHING""",
        (scheme_id, as_of_date, scheme_type, category, sub_category, raw_header_string),
    )


def upsert_plan(
    conn: sqlite3.Connection,
    plan_id: str,
    scheme_id: str,
    amfi_scheme_code: str,
    isin: str | None,
    plan_type: str | None,
    option_type: str | None,
    idcw_variant: str | None,
    seen_date: str,
) -> None:
    conn.execute(
        """INSERT INTO plan (plan_id, scheme_id, amfi_scheme_code, isin, plan_type, option_type,
                              idcw_variant, active, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
           ON CONFLICT(plan_id) DO UPDATE SET
             scheme_id=excluded.scheme_id, isin=excluded.isin,
             plan_type=excluded.plan_type, option_type=excluded.option_type,
             idcw_variant=excluded.idcw_variant, active=1, last_seen=excluded.last_seen""",
        (plan_id, scheme_id, amfi_scheme_code, isin, plan_type, option_type,
         idcw_variant, seen_date, seen_date),
    )


def mark_inactive_plans_not_seen_since(conn: sqlite3.Connection, seen_date: str) -> None:
    """Survivorship-bias guard: schemes absent from today's NAVAll.txt are marked inactive,
    never deleted — historical rows must remain queryable."""
    conn.execute(
        "UPDATE plan SET active = 0 WHERE last_seen < ? AND active = 1",
        (seen_date,),
    )
    conn.execute(
        "UPDATE scheme SET active = 0 WHERE last_seen < ? AND active = 1",
        (seen_date,),
    )


def insert_nav_points(conn: sqlite3.Connection, rows: list[tuple[str, str, float]]) -> None:
    """rows: (plan_id, date, nav). Bulk insert, idempotent."""
    conn.executemany(
        """INSERT INTO nav_point (plan_id, date, nav) VALUES (?, ?, ?)
           ON CONFLICT(plan_id, date) DO UPDATE SET nav=excluded.nav""",
        rows,
    )


def find_plans_by_query(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[sqlite3.Row]:
    like = f"%{query}%"
    return conn.execute(
        """SELECT p.*, s.name AS scheme_name, s.category, s.sub_category, s.scheme_type,
                  s.inception_date, s.active AS scheme_active, a.name AS amc_name
           FROM plan p
           JOIN scheme s ON s.scheme_id = p.scheme_id
           JOIN amc a ON a.amc_id = s.amc_id
           WHERE p.isin = ? OR p.amfi_scheme_code = ? OR s.name LIKE ? OR a.name LIKE ?
           ORDER BY p.active DESC, s.active DESC
           LIMIT ?""",
        (query, query, like, like, limit),
    ).fetchall()


def get_plans_for_scheme(conn: sqlite3.Connection, scheme_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM plan WHERE scheme_id = ?", (scheme_id,)).fetchall()


def get_nav_series(conn: sqlite3.Connection, plan_id: str, start: str | None = None, end: str | None = None):
    q = "SELECT date, nav FROM nav_point WHERE plan_id = ?"
    params: list = [plan_id]
    if start:
        q += " AND date >= ?"
        params.append(start)
    if end:
        q += " AND date <= ?"
        params.append(end)
    q += " ORDER BY date ASC"
    return conn.execute(q, params).fetchall()


def latest_taxonomy(conn: sqlite3.Connection, scheme_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM scheme_taxonomy_history WHERE scheme_id = ?
           ORDER BY as_of_date DESC LIMIT 1""",
        (scheme_id,),
    ).fetchone()
