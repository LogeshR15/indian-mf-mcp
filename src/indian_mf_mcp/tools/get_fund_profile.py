"""get_fund_profile: everything static or slow-moving about the fund — the "what am I buying"
payload (spec §5.2). Mandate stays verbatim text with page anchors, never parsed fields
(spec §1(2): investment-process prose is readable, not structurally extractable without
producing confident nonsense).
"""
from __future__ import annotations

import sqlite3
from datetime import date

from indian_mf_mcp.analytics import cost as cost_mod
from indian_mf_mcp.analytics.returns import from_rows
from indian_mf_mcp.provenance.wrapper import ProvenanceBuilder
from indian_mf_mcp.store import repository as repo

DEFAULT_SECTIONS = ["identity", "mandate", "benchmark", "costs", "managers", "documents"]


def _identity(pb: ProvenanceBuilder, conn, scheme_id: str, plans) -> None:
    scheme_row = conn.execute("SELECT * FROM scheme WHERE scheme_id = ?", (scheme_id,)).fetchone()
    if scheme_row is None:
        return
    amc_row = conn.execute("SELECT * FROM amc WHERE amc_id = ?", (scheme_row["amc_id"],)).fetchone()
    src = pb.add_source(type="amfi_navall", scheme_id=scheme_id)
    pb.fact("name", scheme_row["name"], src, "official")
    pb.fact("amc", amc_row["name"] if amc_row else None, src, "official")
    pb.fact("category", scheme_row["category"], src, "official")
    pb.fact("sub_category", scheme_row["sub_category"], src, "official")
    pb.fact("scheme_type", scheme_row["scheme_type"], src, "official")
    pb.fact("active", bool(scheme_row["active"]), src, "official")
    pb.fact("inception_date", scheme_row["inception_date"], src, "official",
            caveat=None if scheme_row["inception_date"] else
                   "Not captured by the AMFI NAVAll.txt ingest path; requires SID/factsheet extraction.")
    pb.fact("plans", [
        {"plan_id": p["plan_id"], "amfi_scheme_code": p["amfi_scheme_code"], "isin": p["isin"],
         "plan_type": p["plan_type"], "option_type": p["option_type"],
         "idcw_variant": p["idcw_variant"], "active": bool(p["active"])}
        for p in plans
    ], src, "official")


def _latest_sid_doc(conn, scheme_id: str):
    return conn.execute(
        "SELECT * FROM document WHERE scheme_id = ? AND doc_type = 'SID' ORDER BY doc_date DESC LIMIT 1",
        (scheme_id,),
    ).fetchone()


def _mandate(pb: ProvenanceBuilder, conn, scheme_id: str) -> None:
    doc = _latest_sid_doc(conn, scheme_id)
    if doc is None:
        pb.warn(f"No SID registered for {scheme_id}; mandate section unavailable "
                "(not fabricated). Ingest a SID via document_ingest to populate this.")
        return
    rows = conn.execute(
        """SELECT page_number, headings_json, text FROM document_section
           WHERE doc_id = ? ORDER BY page_number""",
        (doc["doc_id"],),
    ).fetchall()
    import json as _json
    objective_pages = [r for r in rows if "Investment Objective" in _json.loads(r["headings_json"] or "[]")]
    strategy_pages = [r for r in rows if "Investment Strategy" in _json.loads(r["headings_json"] or "[]")]
    src = pb.add_source(type="sid", doc_id=doc["doc_id"], doc_date=doc["doc_date"],
                         source_url=doc["source_url"])
    pb.fact("objective_excerpt", [
        {"page": r["page_number"], "text": r["text"][:3000]} for r in objective_pages[:2]
    ], src, "official", caveat="Verbatim excerpt; not a parsed field. Read in full via get_document.")
    pb.fact("strategy_excerpt", [
        {"page": r["page_number"], "text": r["text"][:3000]} for r in strategy_pages[:2]
    ], src, "official", caveat="Verbatim excerpt; not a parsed field. Read in full via get_document.")
    if not objective_pages and not strategy_pages:
        pb.warn(f"SID {doc['doc_id']} registered but no Investment Objective/Strategy heading "
                "detected on any page; heading detection is best-effort (umbrella SIDs can miss). "
                "Use get_document(query=...) directly against this doc_id instead.")


def _benchmark(pb: ProvenanceBuilder, conn, scheme_id: str) -> None:
    snap = conn.execute(
        "SELECT * FROM portfolio_snapshot WHERE scheme_id = ? ORDER BY as_of_date DESC LIMIT 1",
        (scheme_id,),
    ).fetchone()
    if snap is None or not snap["benchmark_name"]:
        pb.warn(f"No portfolio snapshot with a disclosed benchmark name found for {scheme_id}.")
        return
    src = pb.add_source(type="amc_portfolio_disclosure_footer", scheme_id=scheme_id,
                         as_of_date=snap["as_of_date"])
    pb.fact("benchmark_name", snap["benchmark_name"], src, "official")
    pb.fact("is_proxy", False, src, "official")


def _costs(pb: ProvenanceBuilder, conn, scheme_id: str, plans) -> None:
    direct = next((p for p in plans if p["plan_type"] == "Direct" and p["option_type"] == "Growth"), None)
    regular = next((p for p in plans if p["plan_type"] == "Regular" and p["option_type"] == "Growth"), None)
    pb.fact("ter_direct", None, pb.add_source(type="not_implemented"), "official",
            caveat="AMFI's TER page is SPA-only; per-AMC TER capture not yet implemented (Phase 3+).")
    pb.fact("ter_regular", None, pb.add_source(type="not_implemented"), "official",
            caveat="Same as ter_direct.")
    if direct and regular:
        d_series = from_rows(repo.get_nav_series(conn, direct["plan_id"]))
        r_series = from_rows(repo.get_nav_series(conn, regular["plan_id"]))
        spread = cost_mod.realised_direct_regular_spread_bps(d_series, r_series)
        calc = pb.add_calc(method="realised_direct_regular_spread_regression",
                            inputs=["amfi_nav_history"], params={"annualisation": 252})
        pb.fact("realised_direct_regular_spread_bps", spread["annualised_spread_bps"], calc, "calculated",
                caveat="Measured from NAV alone (log-return differential), not a stated TER figure; "
                       f"n_common_days={spread.get('n_common_days')}.")
    else:
        pb.warn("Could not compute realised Direct-vs-Regular spread: one or both "
                "Direct/Regular Growth plans not found for this scheme.")


def _managers(pb: ProvenanceBuilder) -> None:
    pb.warn("Manager identity/tenure requires factsheet extraction, not yet implemented "
            "(spec Phase 3 continuation). Not fabricated.")


def _documents(pb: ProvenanceBuilder, conn, scheme_id: str) -> None:
    rows = conn.execute(
        "SELECT doc_id, doc_type, doc_date, source_url, page_count FROM document WHERE scheme_id = ? ORDER BY doc_date DESC",
        (scheme_id,),
    ).fetchall()
    src = pb.add_source(type="local_document_index", scheme_id=scheme_id)
    pb.fact("documents", [
        {"doc_id": r["doc_id"], "doc_type": r["doc_type"], "doc_date": r["doc_date"],
         "url": r["source_url"], "page_count": r["page_count"]}
        for r in rows
    ], src, "official")


def get_fund_profile(
    conn: sqlite3.Connection,
    scheme_ids: list[str],
    as_of: str | None = None,
    sections: list[str] | None = None,
    provenance: str = "compact",
) -> dict:
    sections = sections or DEFAULT_SECTIONS
    as_of = as_of or date.today().isoformat()
    results = {}

    for scheme_id in scheme_ids:
        pb = ProvenanceBuilder()
        plans = repo.get_plans_for_scheme(conn, scheme_id)
        if not plans:
            results[scheme_id] = {"error": "scheme_not_found", "meta": {"scheme_id": scheme_id}}
            continue

        if "identity" in sections:
            _identity(pb, conn, scheme_id, plans)
        if "mandate" in sections:
            _mandate(pb, conn, scheme_id)
        if "benchmark" in sections:
            _benchmark(pb, conn, scheme_id)
        if "costs" in sections:
            _costs(pb, conn, scheme_id, plans)
        if "managers" in sections:
            _managers(pb)
        if "documents" in sections:
            _documents(pb, conn, scheme_id)

        results[scheme_id] = pb.build(provenance=provenance, extra_meta={"scheme_id": scheme_id, "as_of": as_of})

    return results
