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

    backfill = sub.add_parser("backfill-portfolio", help="Backfill an AMC's portfolio disclosures")
    backfill.add_argument("--amc", required=True,
                           choices=["ppfas", "sbi", "uti", "mirae", "motilal-oswal", "tata"],
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
