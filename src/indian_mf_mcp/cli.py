"""CLI: mf-mcp serve / ingest-navall / backfill-* (Phase 1+2+3)."""
from __future__ import annotations

import argparse
import json
import sys

_AMC_CHOICES = [
    "ppfas", "sbi", "uti", "mirae", "motilal-oswal", "tata",
    "nippon", "dsp", "franklin-templeton", "baroda-bnp-paribas",
    "sundaram", "union", "lic", "taurus", "bank-of-india",
    "hdfc", "quant", "navi", "zerodha", "axis", "kotak",
    "icici-prudential", "quantum", "360-one", "groww", "trust", "bajaj-finserv",
]


def main() -> None:
    parser = argparse.ArgumentParser(prog="mf-mcp")
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- Phase 1 ----
    sub.add_parser("serve", help="Run the MCP server (stdio transport)")
    sub.add_parser("ingest-navall", help="Fetch + archive + parse today's AMFI NAVAll.txt")

    navhist = sub.add_parser("backfill-nav-history",
                              help="Backfill historical NAV from AMFI (month by month, resumable)")
    navhist.add_argument("--from", dest="since", required=True, help="YYYY-MM-DD")
    navhist.add_argument("--to", dest="until", default=None,
                          help="YYYY-MM-DD (default: today)")
    navhist.add_argument("--universes", default="1,2,3",
                          help="AMFI tp values: 1=open-ended, 2=close-ended, 3=interval")
    navhist.add_argument("--force", action="store_true",
                          help="Re-fetch months already recorded as ingested")

    # ---- Phase 2 ----
    backfill = sub.add_parser("backfill-portfolio", help="Backfill an AMC's portfolio disclosures")
    backfill.add_argument("--amc", required=True, choices=_AMC_CHOICES,
                           help="AMC adapter to use")
    backfill.add_argument("--scheme-id", required=True, help="scheme_id from resolve_fund")
    backfill.add_argument("--scheme-hint", required=True,
                           help="Canonical scheme name as it appears in the AMC's own filenames, "
                                "e.g. 'Parag Parikh Flexi Cap Fund'")
    backfill.add_argument("--from", dest="since", required=True, help="YYYY-MM-DD")

    # ---- Phase 3: SID ingest ----
    ingest_sid = sub.add_parser(
        "ingest-sid",
        help="Fetch and ingest a SID PDF by URL (makes it available to get_document)",
    )
    ingest_sid.add_argument("--url", required=True, help="Direct URL to the SID PDF")
    ingest_sid.add_argument("--scheme-id", required=True, help="scheme_id to associate with")
    ingest_sid.add_argument("--date", dest="doc_date", default=None,
                             help="Document date YYYY-MM-DD (inferred from URL if omitted)")

    # ---- Phase 3: single factsheet ingest ----
    ingest_fs = sub.add_parser(
        "ingest-factsheet",
        help="Fetch and ingest a factsheet PDF (extracts managers + TER, detects changes)",
    )
    ingest_fs.add_argument("--url", required=True, help="Direct URL to the factsheet PDF")
    ingest_fs.add_argument("--scheme-id", required=True, help="scheme_id to associate with")
    ingest_fs.add_argument("--date", dest="doc_date", default=None,
                            help="Document date YYYY-MM-DD (inferred from URL if omitted)")

    # ---- Phase 3: bulk factsheet backfill (adapter-driven) ----
    backfill_fs = sub.add_parser(
        "backfill-factsheets",
        help="Backfill factsheets for a scheme via its AMC adapter (extracts managers + TER)",
    )
    backfill_fs.add_argument("--amc", required=True, choices=_AMC_CHOICES,
                              help="AMC adapter to use for discovery")
    backfill_fs.add_argument("--scheme-id", required=True, help="scheme_id from resolve_fund")
    backfill_fs.add_argument("--scheme-hint", required=True,
                              help="Scheme name hint for the adapter's document listing")
    backfill_fs.add_argument("--from", dest="since", required=True, help="YYYY-MM-DD")

    args = parser.parse_args()

    if args.command == "serve":
        from indian_mf_mcp.server import main as serve_main
        serve_main()

    elif args.command == "ingest-navall":
        from indian_mf_mcp.ingest.amfi_navall import run_daily_ingest
        from indian_mf_mcp.store.db import connect

        with connect() as conn:
            stats = run_daily_ingest(conn)
        json.dump(stats, sys.stdout, indent=2)
        print()
        if stats.get("warnings"):
            for warning in stats["warnings"]:
                print(f"warning: {warning}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "backfill-nav-history":
        from datetime import date as _date

        from indian_mf_mcp.ingest.amfi_nav_history import backfill_nav_history
        from indian_mf_mcp.store.db import connect

        start = _date.fromisoformat(args.since)
        end = _date.fromisoformat(args.until) if args.until else _date.today()
        universes = tuple(u.strip() for u in args.universes.split(",") if u.strip())
        with connect() as conn:
            stats = backfill_nav_history(
                conn, start, end, universes=universes, force=args.force,
                progress=lambda msg: print(msg, file=sys.stderr, flush=True),
            )
        json.dump(stats, sys.stdout, indent=2)
        print()
        if stats.get("warnings"):
            for warning in stats["warnings"]:
                print(f"warning: {warning}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "backfill-portfolio":
        from datetime import date as _date

        from indian_mf_mcp.ingest.amc_adapters.registry import get_adapter
        from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
        from indian_mf_mcp.store.db import connect

        adapter, sheet_resolver = get_adapter(args.amc)
        since = _date.fromisoformat(args.since)
        with connect() as conn:
            stats = ingest_scheme_portfolios(conn, adapter, args.scheme_id, args.scheme_hint, since,
                                              sheet_resolver=sheet_resolver)
        json.dump(stats, sys.stdout, indent=2)
        print()

    elif args.command == "ingest-sid":
        from indian_mf_mcp.ingest.factsheet_ingest import ingest_sid_from_url
        from indian_mf_mcp.store.db import connect

        with connect() as conn:
            doc_id = ingest_sid_from_url(
                conn, args.url, args.scheme_id, doc_date=args.doc_date
            )
        json.dump({"doc_id": doc_id, "url": args.url, "scheme_id": args.scheme_id}, sys.stdout, indent=2)
        print()

    elif args.command == "ingest-factsheet":
        from indian_mf_mcp.ingest.factsheet_ingest import ingest_factsheet_from_url
        from indian_mf_mcp.store.db import connect

        with connect() as conn:
            stats = ingest_factsheet_from_url(
                conn, args.url, args.scheme_id, doc_date=args.doc_date
            )
        json.dump(stats, sys.stdout, indent=2)
        print()
        if stats.get("warnings"):
            for warning in stats["warnings"]:
                print(f"warning: {warning}", file=sys.stderr)

    elif args.command == "backfill-factsheets":
        from datetime import date as _date

        from indian_mf_mcp.ingest.amc_adapters.base import DocType
        from indian_mf_mcp.ingest.amc_adapters.registry import get_adapter
        from indian_mf_mcp.ingest.factsheet_ingest import ingest_factsheet
        from indian_mf_mcp.store.db import connect

        adapter, _ = get_adapter(args.amc)
        since = _date.fromisoformat(args.since)

        refs = adapter.list_documents(
            DocType.FACTSHEET, since=since, scheme_hint=args.scheme_hint
        )
        print(f"Found {len(refs)} factsheet(s) since {since}.", file=sys.stderr)

        totals = {"ingested": 0, "skipped": 0, "errors": []}
        with connect() as conn:
            for ref in refs:
                try:
                    raw = adapter.fetch(ref)
                    doc_date = ref.as_of_date.isoformat() if ref.as_of_date else None
                    stats = ingest_factsheet(conn, raw, ref.url, args.scheme_id, doc_date=doc_date)
                    totals["ingested"] += 1
                    print(
                        f"  {ref.as_of_date or '?'}: managers={stats['managers_found']} "
                        f"ter={stats['ter_entries']} events={stats['change_events']}",
                        file=sys.stderr,
                        flush=True,
                    )
                    if stats.get("warnings"):
                        for w in stats["warnings"]:
                            print(f"    warning: {w}", file=sys.stderr)
                except Exception as exc:  # noqa: BLE001
                    totals["skipped"] += 1
                    totals["errors"].append({"url": ref.url, "error": str(exc)})
                    print(f"  FAILED {ref.url}: {exc}", file=sys.stderr)

        json.dump(totals, sys.stdout, indent=2)
        print()


if __name__ == "__main__":
    main()
