"""resolve_fund: turn a free-text query / ISIN / scheme code into unambiguous scheme+plan identity.

This is the documented first call — Indian scheme names and plan/option permutations are a
minefield (spec §5.2). Everything here is derived from AMFI's own NAVAll.txt, so results are
`official` at the field level, with plan/option parsing marked `inferred` (see normalize/taxonomy.py).
"""
from __future__ import annotations

import sqlite3

from indian_mf_mcp.store import repository as repo


def _confidence_for(row: sqlite3.Row, query: str) -> float:
    q = query.strip().lower()
    if row["isin"] and row["isin"].lower() == q:
        return 1.0
    if row["amfi_scheme_code"] and row["amfi_scheme_code"].lower() == q:
        return 1.0
    name = (row["scheme_name"] or "").lower()
    if q == name:
        return 0.95
    if q in name:
        return 0.7
    return 0.5


def _candidate_for_scheme(conn: sqlite3.Connection, rows: list[sqlite3.Row], query: str) -> dict:
    first = rows[0]
    plans = []
    for r in rows:
        plans.append({
            "plan_id": r["plan_id"],
            "amfi_scheme_code": r["amfi_scheme_code"],
            "isin": r["isin"],
            "plan_type": r["plan_type"],
            "option_type": r["option_type"],
            "idcw_variant": r["idcw_variant"],
            "active": bool(r["active"]),
        })
    return {
        "scheme_id": first["scheme_id"],
        "canonical_name": first["scheme_name"],
        "amc": first["amc_name"],
        "category": first["category"],
        "sub_category": first["sub_category"],
        "scheme_type": first["scheme_type"],
        "inception_date": first["inception_date"],
        "active": bool(first["scheme_active"]),
        "plans": plans,
        "confidence": max(_confidence_for(r, query) for r in rows),
    }


def resolve_one(conn: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    if not query or not query.strip():
        return []
    rows = repo.find_plans_by_query(conn, query, limit=limit * 8)
    by_scheme: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_scheme.setdefault(r["scheme_id"], []).append(r)
    candidates = [_candidate_for_scheme(conn, rs, query) for rs in by_scheme.values()]
    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates[:limit]


def resolve_fund(conn: sqlite3.Connection, query, limit: int = 10) -> dict:
    queries = query if isinstance(query, list) else [query]
    results = {}
    for q in queries:
        candidates = resolve_one(conn, q, limit)
        results[q] = candidates
        if not candidates:
            results.setdefault("_warnings", []).append(
                f"No match found for {q!r}. Do not assume the fund does not exist — "
                "the local index may not yet include it; verify spelling or try an ISIN/scheme code."
            )
    return results
