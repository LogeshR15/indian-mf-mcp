"""list_disclosure_events: return detected ChangeEvents for one or more schemes.

Phase 4 tool in the spec (§5.2), but implemented now because the ChangeEvent infrastructure
is already built in Phase 3 (manager/TER change detection during factsheet ingest). This tool
surfaces what the ingest pipeline has already recorded — it does NOT do live detection.

Events are returned newest-first. Claude can use this to:
  - Detect manager changes and split a NAV series at the change date
  - Track TER movements over time (did costs go up after AUM grew?)
  - Identify benchmark / category / mandate changes (once those detectors are wired)

Epistemic status: 'observed' for factsheet-diff-derived events; 'official' only when the
evidence_doc is an addendum (the legal event). Claude must not treat 'observed' events as
confirmed facts — they are "the factsheet changed; the source of that change is inferred."
"""
from __future__ import annotations

import json
import sqlite3

from indian_mf_mcp.provenance.wrapper import ProvenanceBuilder
from indian_mf_mcp.store import manager_repository as mrep

SUPPORTED_EVENT_TYPES = [
    "manager_change",
    "ter_change",
    "benchmark_change",
    "category_change",
    "mandate_revision",
    "addendum",
]


def list_disclosure_events(
    conn: sqlite3.Connection,
    scheme_ids: list[str],
    event_types: list[str] | None = None,
    since: str | None = None,
    provenance: str = "compact",
) -> dict:
    """Return ChangeEvents for the requested schemes, newest-first.

    Args:
        scheme_ids: list of scheme_id strings (from resolve_fund)
        event_types: filter to specific event types (default: all)
        since: ISO date string — only events detected on/after this date
        provenance: "compact"|"full"|"none"
    """
    event_types = event_types or SUPPORTED_EVENT_TYPES
    results = {}

    for scheme_id in scheme_ids:
        pb = ProvenanceBuilder()

        all_events = []
        for et in event_types:
            rows = mrep.get_change_events(conn, scheme_id, event_type=et, since=since)
            for row in rows:
                all_events.append({
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "effective_date": row["effective_date"],
                    "detected_date": row["detected_date"],
                    "detected_from": row["detected_from"],
                    "before": json.loads(row["before_json"]) if row["before_json"] else None,
                    "after": json.loads(row["after_json"]) if row["after_json"] else None,
                    "evidence_doc_id": row["evidence_doc_id"],
                    "confidence": row["confidence"],
                })

        # Sort newest first by detected_date
        all_events.sort(key=lambda e: (e["detected_date"] or ""), reverse=True)

        src = pb.add_source(
            type="change_event_table",
            scheme_id=scheme_id,
            event_types_requested=event_types,
            since=since,
        )

        if not all_events:
            pb.warn(
                f"No ChangeEvents found for {scheme_id} matching the requested filters. "
                "This means either: (a) no factsheets have been ingested yet for this scheme, "
                "or (b) no changes were detected across the ingested factsheets. "
                "Run `mf-mcp backfill-factsheets` to populate the event history."
            )

        pb.fact("events", all_events, src, "observed",
                caveat=(
                    "Events with confidence='observed' are derived by diffing consecutive factsheets. "
                    "They indicate that a field changed between two monthly documents — the exact "
                    "effective_date may differ from the detected_date. "
                    "Only events with confidence='official' (from addenda) are legally authoritative."
                ))

        # Also include a manager timeline summary for convenience
        all_assignments = mrep.get_managers_for_scheme(conn, scheme_id)
        if all_assignments:
            mgr_src = pb.add_source(type="manager_assignment_table", scheme_id=scheme_id)
            pb.fact("manager_timeline", [
                {
                    "name": r["name_normalised"],
                    "from_date": r["from_date"],
                    "to_date": r["to_date"],
                    "current": r["to_date"] is None,
                    "confidence": r["confidence"],
                    "evidence_doc_id": r["evidence_doc_id"],
                }
                for r in all_assignments
            ], mgr_src, "observed",
            caveat="Derived from factsheet archive. 'from_date' is earliest factsheet where "
                   "the manager name appears; 'to_date' is the last factsheet before they stop "
                   "appearing. Not a legally authoritative record.")

        results[scheme_id] = pb.build(
            provenance=provenance,
            extra_meta={
                "scheme_id": scheme_id,
                "n_events": len(all_events),
                "event_types_queried": event_types,
                "since": since,
            },
        )

    return results
