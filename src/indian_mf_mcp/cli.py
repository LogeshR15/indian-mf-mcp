"""CLI: mf-mcp serve / ingest-navall / backfill-* (Phase 1+2+3+4)."""
from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import sys

_AMC_CHOICES = [
    "ppfas", "sbi", "uti", "mirae", "motilal-oswal", "tata",
    "nippon", "dsp", "franklin-templeton", "baroda-bnp-paribas",
    "sundaram", "union", "lic", "taurus", "bank-of-india",
    "hdfc", "quant", "navi", "zerodha", "axis", "kotak",
    "icici-prudential", "quantum", "360-one", "groww", "trust", "bajaj-finserv",
    "iti", "invesco", "bandhan", "hsbc",
]


def _amc_ids(adapter_key: str) -> list[str]:
    from indian_mf_mcp.ingest.amc_identity import amc_ids_for_adapter
    return amc_ids_for_adapter(adapter_key)


def _cmd_resolve(args) -> None:
    """Print scheme_ids for a query.

    Exists because every backfill command takes --scheme-id, but scheme_id was previously
    obtainable only by starting the MCP server and asking a model — a chicken-and-egg that
    made the documented setup path impossible to follow from a shell.
    """
    from indian_mf_mcp.store.db import connect
    from indian_mf_mcp.tools.resolve_fund import resolve_fund

    with connect() as conn:
        payload = resolve_fund(conn, args.query, limit=args.limit)

    if args.as_json:
        json.dump(payload, sys.stdout, indent=2)
        print()
        return

    candidates = payload.get(args.query) or []
    for warning in payload.get("_warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    if not candidates:
        sys.exit(1)

    for cand in candidates:
        plans = cand["plans"]
        avail = cand["availability"]
        have = [name for name, key in (
            ("portfolio", "has_portfolio"), ("factsheet", "has_factsheet"),
            ("documents", "has_documents"),
        ) if avail[key]]
        print(f"{cand['canonical_name']}")
        print(f"  scheme_id    {cand['scheme_id']}")
        print(f"  amc          {cand['amc']}")
        print(f"  category     {cand['category']} / {cand['sub_category']}")
        print(f"  plans        {len(plans)}  "
              + ", ".join(
                  f"{p['plan_type'] or '?'}/{p['option_type'] or '?'}"
                  for p in plans[:4]
              )
              + (" ..." if len(plans) > 4 else ""))
        print(f"  nav through  {avail['nav_coverage_end'] or 'none'}")
        print(f"  also loaded  {', '.join(have) if have else 'nav only'}")
        print()


def _cmd_setup(args) -> None:
    """Run the ingest steps a usable store needs, in dependency order, resumably.

    Each step is skipped when already satisfied, so rerunning after an interrupted run
    (or to widen --years) costs only the work that is actually missing.
    """
    from datetime import date as _date, timedelta

    from indian_mf_mcp.store import inventory
    from indian_mf_mcp.store.db import connect

    def step(n: int, total: int, msg: str) -> None:
        print(f"[{n}/{total}] {msg}", file=sys.stderr, flush=True)

    total = 2 if args.skip_nav_history else 3

    # --- 1. scheme universe -------------------------------------------------
    step(1, total, "Scheme universe — fetching AMFI NAVAll.txt ...")
    from indian_mf_mcp.ingest.amfi_navall import run_daily_ingest

    with connect() as conn:
        stats = run_daily_ingest(conn)
        conn.execute("ANALYZE")
    if stats.get("warnings"):
        for w in stats["warnings"]:
            print(f"      warning: {w}", file=sys.stderr)
    print(
        f"      {stats['schemes']:,} schemes / {stats['plans']:,} plans "
        f"across {stats['amcs']} AMCs",
        file=sys.stderr,
    )

    # --- 2. cap list --------------------------------------------------------
    step(2, total, "AMFI cap list — for market-cap allocation ...")
    from indian_mf_mcp.ingest.amfi_caplist import fetch_and_store_caplist

    try:
        with connect() as conn:
            cap = fetch_and_store_caplist(conn)
        if cap.get("skipped"):
            print("      already current", file=sys.stderr)
        else:
            print(
                f"      {cap['versions_loaded']} of {cap['versions_discovered']} "
                f"half-yearly lists loaded, {cap['isin_count']:,} ISINs, "
                f"latest effective {cap['effective_date']}",
                file=sys.stderr,
            )
        for err in cap.get("errors", []):
            print(f"      warning: {err['url'].split('/')[-1]}: {err['error']}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        # A cap-list failure must not abort setup: it degrades exactly one section of one
        # tool, which already reports itself as unavailable.
        print(f"      failed: {exc}", file=sys.stderr)
        print("      market-cap allocation stays unavailable; "
              "retry later with 'mf-mcp update-caplist'", file=sys.stderr)

    # --- 3. NAV history -----------------------------------------------------
    if args.skip_nav_history:
        print("\nSkipped NAV history. resolve_fund and get_fund_profile work; "
              "return/risk analytics need it.", file=sys.stderr)
    else:
        since = _date.today() - timedelta(days=round(args.years * 365.25))
        step(3, total, f"NAV history — {args.years}y from {since} (resumable; Ctrl-C is safe) ...")
        from indian_mf_mcp.ingest.amfi_nav_history import backfill_nav_history

        with connect() as conn:
            hist = backfill_nav_history(
                conn, since, _date.today(), universes=("1", "2", "3"), force=False,
                progress=lambda msg: print(f"      {msg}", file=sys.stderr, flush=True),
            )
            conn.execute("ANALYZE")
        for w in hist.get("warnings", []):
            print(f"      warning: {w}", file=sys.stderr)

    # --- report -------------------------------------------------------------
    with connect() as conn:
        inv = inventory.collect(conn)
    steps = inventory.next_steps(inv)
    print()
    print(inventory.format_report(inv, steps))
    print()
    print("Connect it to Claude Code:", file=sys.stderr)
    # shlex.quote because the checkout path routinely contains spaces; an unquoted
    # --directory silently truncates the path and the client fails with a confusing
    # "no such directory" long after setup reported success.
    print("  claude mcp add indian-mf -- uv run --directory "
          f"{shlex.quote(str(pathlib.Path.cwd()))} mf-mcp serve", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mf-mcp",
        description="Indian mutual fund evidence store. First run: 'mf-mcp setup', "
                    "then 'mf-mcp serve'. 'mf-mcp status' shows what is loaded.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- Onboarding ----
    setup_cmd = sub.add_parser(
        "setup",
        help="One-command first-run bootstrap: scheme universe + cap list + NAV history",
    )
    setup_cmd.add_argument(
        "--years", type=int, default=3,
        help="Years of daily NAV history to backfill (default: 3, ~8 min). "
             "Use 10 for the full decade (~35 min). Resumable — rerun with a larger "
             "value later to extend.",
    )
    setup_cmd.add_argument(
        "--skip-nav-history", action="store_true",
        help="Stop after the scheme universe and cap list (seconds, no long download). "
             "resolve_fund works; return analytics will not.",
    )

    sub.add_parser("status", help="Show what is ingested and what to run next")

    resolve_cmd = sub.add_parser(
        "resolve",
        help="Look up a fund by name/ISIN/scheme code and print its scheme_id",
    )
    resolve_cmd.add_argument("query", help="Fund name, ISIN, or AMFI scheme code")
    resolve_cmd.add_argument("--limit", type=int, default=5, help="Max matches (default: 5)")
    resolve_cmd.add_argument("--json", action="store_true", dest="as_json",
                             help="Emit the raw resolve_fund payload instead of a table")

    sub.add_parser("amcs", help="List AMC keys accepted by --amc, and their coverage")

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

    elif args.command == "setup":
        _cmd_setup(args)

    elif args.command == "status":
        from indian_mf_mcp.store import inventory
        from indian_mf_mcp.store.db import connect

        with connect() as conn:
            inv = inventory.collect(conn)
        steps = inventory.next_steps(inv)
        print(inventory.format_report(inv, steps))
        # Exit 1 when the store cannot answer anything at all, so a provisioning script
        # or CI check can gate on it. A partially-populated store is exit 0: "no portfolio
        # data for the AMCs you skipped" is a legitimate steady state, not a failure.
        if not inv["plans"]:
            sys.exit(1)

    elif args.command == "resolve":
        _cmd_resolve(args)

    elif args.command == "amcs":
        from indian_mf_mcp.ingest.amc_identity import AMFI_AMC_NAME
        from indian_mf_mcp.ingest.amc_adapters.registry import list_amc_ids

        print(f"{'--amc key':<22} {'AMFI name':<34} portfolio schemes loaded")
        print("-" * 80)
        from indian_mf_mcp.ingest.amc_scheme_registry import get_schemes_for_amc
        from indian_mf_mcp.store.db import connect

        with connect() as conn:
            has_store = conn.execute("SELECT 1 FROM scheme LIMIT 1").fetchone() is not None
            for key in list_amc_ids():
                if has_store:
                    loaded = conn.execute(
                        """SELECT COUNT(DISTINCT ps.scheme_id) FROM portfolio_snapshot ps
                           JOIN scheme s ON s.scheme_id = ps.scheme_id
                           WHERE s.amc_id IN ({})""".format(
                            ",".join("?" * len(_amc_ids(key)))
                        ),
                        _amc_ids(key),
                    ).fetchone()[0]
                    loaded_str = str(loaded)
                else:
                    loaded_str = "-"
                print(f"{key:<22} {AMFI_AMC_NAME[key][:34]:<34} {loaded_str:>8}")
        print(f"\n{len(list_amc_ids())} adapters. Load one with: "
              f"mf-mcp backfill --amc <key> --from 2023-01-01")

    elif args.command == "ingest-navall":
        from indian_mf_mcp.ingest.amfi_navall import run_daily_ingest
        from indian_mf_mcp.store.db import connect

        with connect() as conn:
            stats = run_daily_ingest(conn)
            # SQLite's query planner makes poor index choices on large tables (nav_point is
            # tens of millions of rows) without fresh stats — cheap to run, and this is
            # exactly the workload shape (full-universe scans in get_fund_performance's
            # category comparator) where a stale plan costs seconds per call.
            conn.execute("ANALYZE")
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
            conn.execute("ANALYZE")
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

        from indian_mf_mcp.ingest.amc_adapters.registry import get_adapter
        from indian_mf_mcp.ingest.factsheet_ingest import backfill_factsheets
        from indian_mf_mcp.store.db import connect

        adapter, _ = get_adapter(args.amc)
        since = _date.fromisoformat(args.since)
        with connect() as conn:
            totals = backfill_factsheets(
                conn, adapter, [(args.scheme_id, args.scheme_hint)], since,
                log=lambda m: print(m, file=sys.stderr, flush=True),
            )
        json.dump(totals, sys.stdout, indent=2)
        print()

    # ---- Phase 4 commands ----

    elif args.command == "update-caplist":
        from indian_mf_mcp.ingest.amfi_caplist import fetch_and_store_caplist
        from indian_mf_mcp.store.db import connect

        print("Fetching AMFI stock categorisation list...", file=sys.stderr)
        try:
            with connect() as conn:
                stats = fetch_and_store_caplist(conn)
        except Exception as exc:  # noqa: BLE001
            print(f"error: could not fetch the AMFI cap list: {exc}", file=sys.stderr)
            print(
                "\nThis affects only get_fund_portfolio's market-cap allocation section, "
                "which reports itself as unavailable rather than guessing — every other "
                "tool is unaffected.\nIf this is a 404, AMFI has moved the file: see "
                "docs/amc-coverage.md and open an issue with the new URL.",
                file=sys.stderr,
            )
            sys.exit(1)
        json.dump(stats, sys.stdout, indent=2)
        print()
        if stats.get("skipped"):
            print("Cap lists unchanged (same sha256). No update needed.", file=sys.stderr)
        else:
            print(
                f"Loaded {stats['versions_loaded']} of {stats['versions_discovered']} "
                f"half-yearly cap lists ({stats['isin_count']:,} ISIN rows); "
                f"latest effective_date={stats['effective_date']}",
                file=sys.stderr,
            )
        for err in stats.get("errors", []):
            print(f"warning: {err['url']}: {err['error']}", file=sys.stderr)

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
        from indian_mf_mcp.ingest.factsheet_ingest import backfill_factsheets
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
            "factsheet": {},
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

            if "factsheet" in doc_types:
                fs = backfill_factsheets(
                    conn, adapter, [(sc["scheme_id"], sc["adapter_hint"]) for sc in schemes],
                    since, log=lambda m: print(f"    factsheet: {m}", file=sys.stderr, flush=True),
                )
                totals["factsheet"] = fs

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
