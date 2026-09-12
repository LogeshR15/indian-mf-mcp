"""Persistence helpers for portfolio snapshots, holdings, and derivative positions."""
from __future__ import annotations

import sqlite3

from indian_mf_mcp.parsers.xlsx_portfolio import PortfolioParseResult


def upsert_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: str,
    scheme_id: str,
    as_of_date: str,
    disclosure_type: str,
    source_doc_id: str | None,
    result: PortfolioParseResult,
    retrieved_at: str,
) -> None:
    conn.execute(
        """INSERT INTO portfolio_snapshot
           (snapshot_id, scheme_id, as_of_date, disclosure_type, source_doc_id,
            total_market_value_lakhs, grand_total_pct_nav, reconciliation_ok,
            benchmark_name, retrieved_at, parse_confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(scheme_id, as_of_date, disclosure_type) DO UPDATE SET
             total_market_value_lakhs=excluded.total_market_value_lakhs,
             grand_total_pct_nav=excluded.grand_total_pct_nav,
             reconciliation_ok=excluded.reconciliation_ok,
             benchmark_name=excluded.benchmark_name,
             retrieved_at=excluded.retrieved_at,
             parse_confidence=excluded.parse_confidence""",
        (
            snapshot_id, scheme_id, as_of_date, disclosure_type, source_doc_id,
            result.grand_total_market_value, result.grand_total_pct_nav,
            None if result.reconciliation_ok is None else int(result.reconciliation_ok),
            result.benchmark_name, retrieved_at, result.parse_confidence,
        ),
    )
    # holdings/derivatives are replaced wholesale on re-ingest of the same snapshot
    conn.execute("DELETE FROM holding WHERE snapshot_id = ?", (snapshot_id,))
    conn.execute("DELETE FROM derivative_position WHERE snapshot_id = ?", (snapshot_id,))
    conn.executemany(
        """INSERT INTO holding (snapshot_id, isin, instrument_name, industry_or_rating,
             quantity, market_value_lakhs, pct_nav, asset_class, listed, section_label)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (snapshot_id, h.isin, h.instrument_name, h.industry_or_rating, h.quantity,
             h.market_value_lakhs, h.pct_nav, h.asset_class, int(h.listed), h.section_label)
            for h in result.holdings
        ],
    )
    conn.executemany(
        """INSERT INTO derivative_position
             (snapshot_id, instrument_name, direction, quantity, market_value_lakhs,
              pct_to_aum, section_label)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (snapshot_id, d.instrument_name, d.direction, d.quantity, d.market_value_lakhs,
             d.pct_to_aum, d.section_label)
            for d in result.derivatives
        ],
    )


def get_snapshots_for_scheme(conn: sqlite3.Connection, scheme_id: str, since: str | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM portfolio_snapshot WHERE scheme_id = ?"
    params: list = [scheme_id]
    if since:
        q += " AND as_of_date >= ?"
        params.append(since)
    q += " ORDER BY as_of_date ASC"
    return conn.execute(q, params).fetchall()


def get_latest_snapshot(conn: sqlite3.Connection, scheme_id: str, as_of: str | None = None) -> sqlite3.Row | None:
    q = "SELECT * FROM portfolio_snapshot WHERE scheme_id = ?"
    params: list = [scheme_id]
    if as_of:
        q += " AND as_of_date <= ?"
        params.append(as_of)
    q += " ORDER BY as_of_date DESC LIMIT 1"
    return conn.execute(q, params).fetchone()


def get_holdings(conn: sqlite3.Connection, snapshot_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM holding WHERE snapshot_id = ? ORDER BY pct_nav DESC", (snapshot_id,)
    ).fetchall()


def get_derivatives(conn: sqlite3.Connection, snapshot_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM derivative_position WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchall()
