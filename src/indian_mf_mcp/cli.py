"""CLI: `mf-mcp ingest-navall` (Phase 1), `mf-mcp serve`."""
from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="mf-mcp")
    sub = parser.add_subparsers(dest="command", required=True)

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

    backfill = sub.add_parser("backfill-portfolio", help="Backfill an AMC's portfolio disclosures")
    backfill.add_argument("--amc", required=True,
                           choices=["ppfas", "sbi", "uti", "mirae", "motilal-oswal", "tata",
                                    "nippon", "dsp", "franklin-templeton", "baroda-bnp-paribas",
                                    "sundaram", "union", "lic", "taurus", "bank-of-india",
                                    "hdfc", "quant", "navi", "zerodha", "axis", "kotak", "icici-prudential", "quantum", "360-one", "groww"],
                           help="AMC adapter to use")
    backfill.add_argument("--scheme-id", required=True, help="scheme_id from resolve_fund")
    backfill.add_argument("--scheme-hint", required=True,
                           help="Canonical scheme name as it appears in the AMC's own filenames, "
                                "e.g. 'Parag Parikh Flexi Cap Fund'")
    backfill.add_argument("--from", dest="since", required=True, help="YYYY-MM-DD")

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


if __name__ == "__main__":
    main()
