"""get_fund_portfolio: holdings, allocations, change detection. The heart of the differentiated
value (spec §5.2) — requires per-AMC fetching, XLSX parsing, ISIN-keyed set arithmetic and
corporate-action handling, none of which is Claude's job.

Phase 4 additions:
  - market_cap allocation via AMFI half-yearly cap list (point-in-time safe)
  - portfolio overlap when len(scheme_ids) >= 2 and 'overlap' in sections
  - data_staleness_warning when snapshot is >60 days old and adapter health is known
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from indian_mf_mcp.analytics.overlap import OverlapResult, all_pairs_overlap
from indian_mf_mcp.change_engine.concentration import concentration_stats
from indian_mf_mcp.change_engine.persistence import compute_persistence
from indian_mf_mcp.change_engine.portfolio_diff import detect_disclosure_gap, diff_snapshots
from indian_mf_mcp.ingest import amc_identity
from indian_mf_mcp.ingest.amfi_caplist import compute_market_cap_allocation
from indian_mf_mcp.provenance.wrapper import ProvenanceBuilder
from indian_mf_mcp.store import portfolio_repository as prepo

HISTORY_MONTHS = {"none": 0, "12M": 12, "36M": 36, "60M": 60}

# Warn when the most recent snapshot is older than this many days
_STALENESS_THRESHOLD_DAYS = 60


def _holdings_dicts(rows) -> list[dict]:
    return [{
        "isin": r["isin"], "instrument_name": r["instrument_name"],
        "industry_or_rating": r["industry_or_rating"], "quantity": r["quantity"],
        "market_value_lakhs": r["market_value_lakhs"], "pct_nav": r["pct_nav"],
        "asset_class": r["asset_class"], "listed": bool(r["listed"]),
        "section_label": r["section_label"],
    } for r in rows]


def _allocations(conn: sqlite3.Connection, rows, as_of_date: str) -> dict:
    """Compute sector, asset-class, cash, and market-cap allocations.

    market_cap is now populated via the AMFI half-yearly cap list (Phase 4).
    Falls back gracefully if the cap list has not been downloaded yet.
    """
    by_sector: dict[str, float] = {}
    by_asset_class: dict[str, float] = {}
    cash_pct = 0.0
    for r in rows:
        pct = r["pct_nav"] or 0.0
        ac = r["asset_class"] or "other"
        by_asset_class[ac] = by_asset_class.get(ac, 0.0) + pct
        if ac == "cash":
            cash_pct += pct
        elif r["industry_or_rating"]:
            key = r["industry_or_rating"].strip()
            if key:
                by_sector[key] = by_sector.get(key, 0.0) + pct

    market_cap = compute_market_cap_allocation(conn, rows, as_of_date)

    return {
        "by_sector": dict(sorted(by_sector.items(), key=lambda kv: kv[1], reverse=True)),
        "by_asset_class": by_asset_class,
        "cash_pct": cash_pct,
        "market_cap": market_cap,
    }


def _staleness_warning(conn: sqlite3.Connection, amc_id: str | None, snapshot_date: str) -> str | None:
    """Return a staleness warning string if the snapshot is old and adapter health is known."""
    try:
        today = date.today()
        snap_date = date.fromisoformat(snapshot_date)
        gap = (today - snap_date).days
        if gap <= _STALENESS_THRESHOLD_DAYS:
            return None
        # Check adapter health table for additional context. `adapter_health` is keyed by
        # the adapter's own amc_id ("amc-ppfas"), not the AMFI-derived scheme.amc_id
        # ("amc-ppfas-mutual-fund") we hold here — querying with the latter matched nothing,
        # so this branch never fired and every stale portfolio got the generic message.
        health_ids = amc_identity.health_lookup_ids(amc_id)
        if health_ids:
            placeholders = ",".join("?" * len(health_ids))
            health = conn.execute(
                f"SELECT status, checked_at, error FROM adapter_health "
                f"WHERE amc_id IN ({placeholders}) AND doc_type = 'monthly_portfolio' LIMIT 1",
                health_ids,
            ).fetchone()
            if health:
                status = health["status"]
                checked = (health["checked_at"] or "")[:10]
                err = health["error"] or ""
                return (
                    f"Portfolio data is {gap} days old (last snapshot: {snapshot_date}). "
                    f"Adapter health as of {checked}: {status}"
                    + (f" — {err}" if err and status != "ok" else "")
                    + ". Run 'mf-mcp health' and 'mf-mcp backfill-portfolio' to refresh."
                )
        return (
            f"Portfolio data is {gap} days old (last snapshot: {snapshot_date}). "
            f"Run 'mf-mcp backfill-portfolio' to fetch newer disclosures."
        )
    except Exception:
        return None


def get_fund_portfolio(
    conn: sqlite3.Connection,
    scheme_ids: list[str],
    as_of: str = "latest",
    compare_to: str | None = "prev_month",
    history: str = "none",
    sections: list[str] | None = None,
    holdings_limit: int | None = None,
    provenance: str = "compact",
) -> dict:
    sections = sections or ["holdings", "allocations", "changes", "concentration", "persistence"]
    results: dict = {}

    # Collect holdings for all schemes (needed for overlap)
    scheme_holdings_for_overlap: dict[str, list] = {}

    for scheme_id in scheme_ids:
        pb = ProvenanceBuilder()
        as_of_date = None if as_of == "latest" else as_of
        snapshot = prepo.get_latest_snapshot(conn, scheme_id, as_of=as_of_date)
        if snapshot is None:
            results[scheme_id] = {
                "error": "no_portfolio_data",
                "meta": {"scheme_id": scheme_id,
                         "note": "No parsed portfolio snapshot available for this scheme yet. "
                                 "This may mean the AMC adapter has not been run, not that the "
                                 "fund has no disclosed holdings."},
            }
            continue

        holdings_rows = prepo.get_holdings(conn, snapshot["snapshot_id"])
        scheme_holdings_for_overlap[scheme_id] = holdings_rows

        src_snap = pb.add_source(
            type="amc_portfolio_disclosure", scheme_id=scheme_id,
            as_of_date=snapshot["as_of_date"], doc_id=snapshot["source_doc_id"],
            disclosure_type=snapshot["disclosure_type"],
        )

        # Resolve AMC for staleness check
        amc_row = conn.execute(
            "SELECT amc_id FROM scheme WHERE scheme_id = ?", (scheme_id,)
        ).fetchone()
        amc_id = amc_row["amc_id"] if amc_row else None

        meta = {
            "scheme_id": scheme_id, "as_of_date": snapshot["as_of_date"],
            "disclosure_type": snapshot["disclosure_type"],
            "reconciliation_ok": bool(snapshot["reconciliation_ok"]),
            "benchmark_name": snapshot["benchmark_name"],
        }

        # Staleness warning
        stale_warn = _staleness_warning(conn, amc_id, snapshot["as_of_date"])
        if stale_warn:
            meta["data_staleness_warning"] = stale_warn

        if "holdings" in sections:
            hd = _holdings_dicts(holdings_rows)
            if holdings_limit:
                hd = sorted(hd, key=lambda h: h["pct_nav"] or 0, reverse=True)[:holdings_limit]
            pb.fact("holdings", hd, src_snap, "official")

        if "allocations" in sections:
            alloc = _allocations(conn, holdings_rows, snapshot["as_of_date"])
            calc = pb.add_calc(method="allocation_aggregation", inputs=[src_snap])
            market_cap_caveat = (
                alloc["market_cap"].get("caveat")
                or "market_cap computed from AMFI half-yearly cap list (point-in-time)."
            )
            pb.fact("allocations", alloc, calc, "calculated", caveat=market_cap_caveat)

        if "concentration" in sections:
            equity_like_pcts = [h["pct_nav"] for h in holdings_rows
                                 if h["asset_class"] in ("equity", "foreign", "reit") and h["pct_nav"]]
            calc = pb.add_calc(method="concentration_hhi", inputs=[src_snap],
                                params={"population": "equity_foreign_reit"})
            pb.fact("concentration", concentration_stats(equity_like_pcts), calc, "calculated")

        if "changes" in sections and compare_to:
            if compare_to == "prev_month":
                prev_snapshot = conn.execute(
                    """SELECT * FROM portfolio_snapshot WHERE scheme_id = ? AND as_of_date < ?
                       ORDER BY as_of_date DESC LIMIT 1""",
                    (scheme_id, snapshot["as_of_date"]),
                ).fetchone()
            else:
                prev_snapshot = prepo.get_latest_snapshot(conn, scheme_id, as_of=compare_to)

            if prev_snapshot is None or prev_snapshot["snapshot_id"] == snapshot["snapshot_id"]:
                pb.warn(f"No prior snapshot available to compare against for {scheme_id}; "
                        "'changes' section omitted rather than fabricated.")
            else:
                gap_warning = detect_disclosure_gap(
                    prev_snapshot["as_of_date"], snapshot["as_of_date"],
                    prev_snapshot["disclosure_type"], snapshot["disclosure_type"],
                )
                if gap_warning:
                    pb.warn(gap_warning)
                prev_rows = prepo.get_holdings(conn, prev_snapshot["snapshot_id"])
                change_rows = diff_snapshots(prev_rows, holdings_rows)
                src_prev = pb.add_source(
                    type="amc_portfolio_disclosure", scheme_id=scheme_id,
                    as_of_date=prev_snapshot["as_of_date"], doc_id=prev_snapshot["source_doc_id"],
                )
                calc = pb.add_calc(method="isin_keyed_quantity_diff", inputs=[src_prev, src_snap])
                pb.fact("changes", [
                    {"isin": c.isin, "name": c.name, "action": c.action,
                     "delta_qty": c.delta_qty, "delta_pct_nav": c.delta_pct_nav,
                     "delta_value_lakhs": c.delta_value, "confidence": c.confidence,
                     "flags": c.flags}
                    for c in change_rows
                ], calc, "observed" if not any(c.flags for c in change_rows) else "approximation",
                   caveat="Quantity-based; corporate actions are flagged, not asserted as trades. "
                          "PRICE_FLOW_DRIFT rows reflect price/inflow movement with no trade.")

        if "persistence" in sections:
            months = HISTORY_MONTHS.get(history, 0)
            if months == 0:
                pb.warn("persistence requested but history='none'; provide history=\"12M\"/"
                        "\"36M\"/\"60M\" for a persistence table.")
            else:
                since_date = (date.fromisoformat(snapshot["as_of_date"])
                              - timedelta(days=months * 31)).isoformat()
                snaps = prepo.get_snapshots_for_scheme(conn, scheme_id, since=since_date)
                snapshots_holdings = [
                    (s["as_of_date"], prepo.get_holdings(conn, s["snapshot_id"])) for s in snaps
                ]
                persistence_rows = compute_persistence(snapshots_holdings)
                calc = pb.add_calc(method="holding_persistence", params={"history": history},
                                    inputs=[src_snap])
                pb.fact("persistence", [
                    {"isin": r.isin, "name": r.name, "months_held": r.months_held,
                     "continuous_streak_current": r.continuous_streak_current,
                     "avg_pct_nav": r.avg_pct_nav, "first_seen_date": r.first_seen_date,
                     "last_seen_date": r.last_seen_date}
                    for r in persistence_rows
                ], calc, "observed",
                   caveat=f"Coverage window is whatever snapshots are actually stored since "
                          f"{since_date}; gaps in AMC disclosure are not interpolated.")
                meta["persistence_coverage_start"] = snaps[0]["as_of_date"] if snaps else None
                meta["persistence_coverage_end"] = snaps[-1]["as_of_date"] if snaps else None
                meta["persistence_n_snapshots"] = len(snaps)

        results[scheme_id] = pb.build(provenance=provenance, extra_meta=meta)

    # ---- Portfolio overlap (Phase 4) ----
    # Computed when >=2 schemes and 'overlap' in sections (or by default when multi-scheme)
    if len(scheme_ids) >= 2 and "overlap" in sections and len(scheme_holdings_for_overlap) >= 2:
        overlap_results = all_pairs_overlap(scheme_holdings_for_overlap)
        results["_overlap"] = {
            "pairs": [
                {
                    "scheme_id_a": r.scheme_id_a,
                    "scheme_id_b": r.scheme_id_b,
                    "overlap_pct_a": r.overlap_pct_a,
                    "overlap_pct_b": r.overlap_pct_b,
                    "overlap_pct_avg": r.overlap_pct_avg,
                    "common_count": r.common_count,
                    "total_count_a": r.total_count_a,
                    "total_count_b": r.total_count_b,
                    "common_holdings": r.common_holdings,
                }
                for r in overlap_results
            ],
            "meta": {
                "kind": "calculated",
                "method": "isin_set_intersection_weighted_pct_nav",
                "population": "equity_foreign_reit_only",
                "caveat": (
                    "Overlap is computed from equity, foreign equity, and REIT holdings only. "
                    "Cash, debt, and derivative positions are excluded. "
                    "ISINs appearing in multiple sub-sections of the same fund are summed. "
                    "overlap_pct_a = sum of fund A's %NAV in ISINs also held by fund B "
                    "(asymmetric: a large fund and a small fund will have very different readings)."
                ),
            },
        }

    return results
