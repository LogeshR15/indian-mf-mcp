"""CLI: mf-mcp serve / ingest-navall / backfill-* (Phase 1+2+3+4)."""
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
    "iti",
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

    # ---- Phase 4: AMFI cap list ----
    sub.add_parser(
        "update-caplist",
        help="Fetch the latest AMFI stock categorisation (cap list) for market-cap allocation",
    )

    # ---- Phase 4: single addendum ingest ----
    ingest_add = sub.add_parser(
        "ingest-addendum",
        help="Fetch and ingest an addendum PDF (extracts official ChangeEvents)",
    )
    ingest_add.add_argument("--url", required=True, help="Direct URL to the addendum PDF")
    ingest_add.add_argument("--scheme-id", required=True, help="scheme_id to associate with")
    ingest_add.add_argument("--date", dest="doc_date", default=None,
                             help="Document date YYYY-MM-DD (inferred from URL if omitted)")

    # ---- Phase 4: bulk addendum backfill ----
    backfill_add = sub.add_parser(
        "backfill-addenda",
        help="Backfill addenda for a scheme via its AMC adapter",
    )
    backfill_add.add_argument("--amc", required=True, choices=_AMC_CHOICES)
    backfill_add.add_argument("--scheme-id", required=True)
    backfill_add.add_argument("--scheme-hint", required=True)
    backfill_add.add_argument("--from", dest="since", required=True, help="YYYY-MM-DD")

    # ---- Phase 4: bulk backfill (AMC-wide, no per-scheme args) ----
    bulk = sub.add_parser(
        "backfill",
        help="Bulk backfill all known schemes for an AMC (portfolio + factsheets)",
    )
    bulk.add_argument("--amc", required=True, choices=_AMC_CHOICES)
    bulk.add_argument("--from", dest="since", required=True, help="YYYY-MM-DD")
    bulk.add_argument(
        "--doc-types",
        default="portfolio,factsheet",
        help="Comma-separated: portfolio,factsheet,addendum (default: portfolio,factsheet)",
    )
    bulk.add_argument(
        "--active-only",
        action="store_true",
        default=True,
        help="Skip inactive schemes (default: True)",
    )

    # ---- Phase 4: adapter health checks ----
    health_cmd = sub.add_parser(
        "health",
        help="Check that AMC adapters can discover recent documents",
    )
    health_cmd.add_argument(
        "--amc",
        default=None,
        choices=_AMC_CHOICES,
        help="Check a single AMC (default: all AMCs)",
    )
    health_cmd.add_argument(
        "--doc-types",
        default="monthly_portfolio,factsheet",
        help="Comma-separated doc_types to check (default: monthly_portfolio,factsheet)",
    )
    health_cmd.add_argument(
        "--save",
        action="store_true",
        help="Persist health results to the database (for staleness warnings in get_fund_portfolio)",
    )

    # ---- Phase 4: register a scheme hint manually ----
    reg_hint = sub.add_parser(
        "register-scheme-hint",
        help="Manually register the adapter hint for a scheme (overrides auto-detected name)",
    )
    reg_hint.add_argument("--scheme-id", required=True)
    reg_hint.add_argument("--amc", required=True, choices=_AMC_CHOICES)
    reg_hint.add_argument("--hint", required=True,
                           help="Scheme name as the adapter's list_documents() recognises it")

    args = parser.parse_args()

    # ====================================================================
    # Command implementations
    # ====================================================================

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

    # ---- Phase 4 commands ----

    elif args.command == "update-caplist":
        from indian_mf_mcp.ingest.amfi_caplist import fetch_and_store_caplist
        from indian_mf_mcp.store.db import connect

        print("Fetching AMFI stock categorisation list...", file=sys.stderr)
        with connect() as conn:
            stats = fetch_and_store_caplist(conn)
        json.dump(stats, sys.stdout, indent=2)
        print()
        if stats.get("skipped"):
            print("Cap list unchanged (same sha256). No update needed.", file=sys.stderr)
        else:
            print(
                f"Stored {stats['isin_count']} ISINs for effective_date={stats['effective_date']}",
                file=sys.stderr,
            )

    elif args.command == "ingest-addendum":
        from indian_mf_mcp.ingest.addendum_ingest import ingest_addendum_from_url
        from indian_mf_mcp.store.db import connect

        with connect() as conn:
            stats = ingest_addendum_from_url(
                conn, args.url, args.scheme_id, doc_date=args.doc_date
            )
        json.dump(stats, sys.stdout, indent=2)
        print()
        if stats.get("warnings"):
            for warning in stats["warnings"]:
                print(f"warning: {warning}", file=sys.stderr)

    elif args.command == "backfill-addenda":
        from datetime import date as _date

        from indian_mf_mcp.ingest.amc_adapters.base import DocType
        from indian_mf_mcp.ingest.amc_adapters.registry import get_adapter
        from indian_mf_mcp.ingest.addendum_ingest import ingest_addendum
        from indian_mf_mcp.store.db import connect

        adapter, _ = get_adapter(args.amc)
        since = _date.fromisoformat(args.since)

        refs = adapter.list_documents(
            DocType.ADDENDUM, since=since, scheme_hint=args.scheme_hint
        )
        print(f"Found {len(refs)} addendum(a) since {since}.", file=sys.stderr)

        totals: dict = {"ingested": 0, "skipped": 0, "total_events": 0, "errors": []}
        with connect() as conn:
            for ref in refs:
                try:
                    raw = adapter.fetch(ref)
                    doc_date = ref.as_of_date.isoformat() if ref.as_of_date else None
                    stats = ingest_addendum(conn, raw, ref.url, args.scheme_id, doc_date=doc_date)
                    if stats.get("skipped"):
                        totals["skipped"] += 1
                    else:
                        totals["ingested"] += 1
                        totals["total_events"] += stats.get("change_events", 0)
                    print(
                        f"  {ref.as_of_date or '?'}: events={stats['change_events']} "
                        f"{'(skipped)' if stats.get('skipped') else ''}",
                        file=sys.stderr, flush=True,
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

    elif args.command == "backfill":
        # Bulk AMC-wide backfill using scheme_adapter_hint registry
        from datetime import date as _date

        from indian_mf_mcp.ingest.amc_adapters.base import DocType
        from indian_mf_mcp.ingest.amc_adapters.registry import get_adapter
        from indian_mf_mcp.ingest.amc_scheme_registry import get_schemes_for_amc
        from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
        from indian_mf_mcp.ingest.factsheet_ingest import ingest_factsheet
        from indian_mf_mcp.ingest.addendum_ingest import ingest_addendum
        from indian_mf_mcp.store.db import connect

        adapter, sheet_resolver = get_adapter(args.amc)
        since = _date.fromisoformat(args.since)
        doc_types = [d.strip() for d in args.doc_types.split(",") if d.strip()]

        with connect() as conn:
            schemes = get_schemes_for_amc(conn, args.amc)

        if not schemes:
            print(
                f"No schemes registered for AMC '{args.amc}'. "
                "Run 'mf-mcp ingest-navall' first to populate the scheme registry, "
                "or use 'mf-mcp register-scheme-hint' to add entries manually.",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.active_only:
            schemes = [s for s in schemes if s.get("active", 1)]

        print(
            f"Bulk backfill: AMC={args.amc}, {len(schemes)} schemes, "
            f"doc_types={doc_types}, since={since}",
            file=sys.stderr,
        )

        totals: dict = {
            "amc": args.amc, "schemes_attempted": len(schemes),
            "portfolio": {"ingested": 0, "errors": []},
            "factsheet": {"ingested": 0, "skipped": 0, "errors": []},
            "addendum": {"ingested": 0, "skipped": 0, "total_events": 0, "errors": []},
        }

        with connect() as conn:
            for i, scheme in enumerate(schemes, 1):
                scheme_id = scheme["scheme_id"]
                hint = scheme["adapter_hint"]
                print(f"  [{i}/{len(schemes)}] {scheme['name']} (hint: {hint!r})", file=sys.stderr)

                if "portfolio" in doc_types:
                    try:
                        stats = ingest_scheme_portfolios(
                            conn, adapter, scheme_id, hint, since,
                            sheet_resolver=sheet_resolver,
                        )
                        totals["portfolio"]["ingested"] += stats.get("ingested", 0)
                        print(f"    portfolio: {stats.get('ingested',0)} ingested", file=sys.stderr)
                    except Exception as exc:  # noqa: BLE001
                        totals["portfolio"]["errors"].append({"scheme_id": scheme_id, "error": str(exc)})
                        print(f"    portfolio ERROR: {exc}", file=sys.stderr)

                if "factsheet" in doc_types:
                    try:
                        refs = adapter.list_documents(DocType.FACTSHEET, since=since, scheme_hint=hint)
                        for ref in refs:
                            raw = adapter.fetch(ref)
                            doc_date = ref.as_of_date.isoformat() if ref.as_of_date else None
                            s = ingest_factsheet(conn, raw, ref.url, scheme_id, doc_date=doc_date)
                            totals["factsheet"]["ingested"] += 1
                        print(f"    factsheet: {len(refs)} processed", file=sys.stderr)
                    except Exception as exc:  # noqa: BLE001
                        totals["factsheet"]["errors"].append({"scheme_id": scheme_id, "error": str(exc)})
                        print(f"    factsheet ERROR: {exc}", file=sys.stderr)

                if "addendum" in doc_types:
                    try:
                        refs = adapter.list_documents(DocType.ADDENDUM, since=since, scheme_hint=hint)
                        for ref in refs:
                            raw = adapter.fetch(ref)
                            doc_date = ref.as_of_date.isoformat() if ref.as_of_date else None
                            s = ingest_addendum(conn, raw, ref.url, scheme_id, doc_date=doc_date)
                            if not s.get("skipped"):
                                totals["addendum"]["ingested"] += 1
                                totals["addendum"]["total_events"] += s.get("change_events", 0)
                            else:
                                totals["addendum"]["skipped"] += 1
                        print(f"    addendum: {len(refs)} processed", file=sys.stderr)
                    except Exception as exc:  # noqa: BLE001
                        totals["addendum"]["errors"].append({"scheme_id": scheme_id, "error": str(exc)})
                        print(f"    addendum ERROR: {exc}", file=sys.stderr)

        json.dump(totals, sys.stdout, indent=2)
        print()

    elif args.command == "health":
        from indian_mf_mcp.ingest.amc_adapters.registry import get_adapter, list_amc_ids
        from indian_mf_mcp.ingest.health_check import (
            run_health_checks, save_health_results, format_health_table,
        )
        from indian_mf_mcp.store.db import connect

        doc_types = [d.strip() for d in args.doc_types.split(",") if d.strip()]

        if args.amc:
            amc_ids = [args.amc]
        else:
            amc_ids = _AMC_CHOICES

        adapters = []
        for amc_id in amc_ids:
            try:
                adapter, _ = get_adapter(amc_id)
                adapters.append(adapter)
            except Exception as exc:
                print(f"warning: could not load adapter for {amc_id}: {exc}", file=sys.stderr)

        print(
            f"Running health checks: {len(adapters)} adapters × {len(doc_types)} doc_types...",
            file=sys.stderr,
        )
        results = run_health_checks(adapters, doc_types=doc_types)

        print(format_health_table(results))

        if args.save:
            with connect() as conn:
                save_health_results(conn, results)
            print(f"\nSaved {len(results)} health records to database.", file=sys.stderr)

        broken = [r for r in results if r.status == "broken"]
        if broken:
            print(
                f"\n{len(broken)} adapter(s) broken: "
                + ", ".join(f"{r.amc_id}/{r.doc_type}" for r in broken),
                file=sys.stderr,
            )
            sys.exit(1)

    elif args.command == "register-scheme-hint":
        from indian_mf_mcp.ingest.amc_scheme_registry import upsert_scheme_hint
        from indian_mf_mcp.store.db import connect

        with connect() as conn:
            upsert_scheme_hint(conn, args.scheme_id, args.amc, args.hint)
            conn.commit()
        json.dump(
            {"scheme_id": args.scheme_id, "amc": args.amc, "hint": args.hint},
            sys.stdout, indent=2,
        )
        print()
        print(f"Registered hint '{args.hint}' for {args.scheme_id} / {args.amc}", file=sys.stderr)


if __name__ == "__main__":
    main()
