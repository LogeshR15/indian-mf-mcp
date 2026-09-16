"""MCP server entrypoint — all six tools (Phases 1-4)."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from indian_mf_mcp.store.db import connect
from indian_mf_mcp.tools.get_document import get_document as _get_document
from indian_mf_mcp.tools.get_fund_performance import get_fund_performance as _get_fund_performance
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio as _get_fund_portfolio
from indian_mf_mcp.tools.get_fund_profile import get_fund_profile as _get_fund_profile
from indian_mf_mcp.tools.list_disclosure_events import list_disclosure_events as _list_disclosure_events
from indian_mf_mcp.tools.resolve_fund import resolve_fund as _resolve_fund

mcp = FastMCP(
    "indian-mf-mcp",
    instructions=(
        "Research and educational use only; not investment advice. This server retrieves, "
        "normalizes and computes evidence about Indian mutual funds — it never scores, rates, "
        "or recommends funds, and it never emits a true performance-attribution decomposition "
        "(not computable from Indian public disclosure). Every fact carries an epistemic tag "
        "(official/calculated/observed/approximation/inferred) in the `data`/`sources` envelope; "
        "weight `approximation` and `inferred` facts accordingly. Call resolve_fund first to "
        "disambiguate scheme names before any other tool."
    ),
)


@mcp.tool()
def resolve_fund(query: str | list[str], limit: int = 10) -> dict:
    """Resolve a fund name, ISIN, or AMFI scheme code to unambiguous scheme+plan identity.

    Always call this first — Indian scheme names are ambiguous (renames, near-identical
    names across AMCs, 4-8 plan/option variants per scheme).
    """
    with connect() as conn:
        return _resolve_fund(conn, query, limit=limit)


@mcp.tool()
def get_fund_performance(
    scheme_ids: list[str],
    plan: str = "direct_growth",
    period: str = "10Y",
    comparators: list[str] | None = None,
    metrics: list[str] | None = None,
    rolling_windows: list[int] | None = None,
    nav_series: bool = False,
    provenance: str = "compact",
    risk_free_annual: float | None = None,
) -> dict:
    """Complete return/risk evidence pack for one or more schemes (list = comparison).

    metrics: any of "trailing","rolling","risk","drawdown","stress".
    comparators: any of "benchmark" (proxied via a passive index fund's NAV for the subset
    of benchmarks with a configured proxy — the payload labels it `is_proxy` and names the
    proxy fund; never presented as the licensed index itself), "category" (computed live
    from the full ingested universe).
    Defaults to the Direct/Growth plan; a warning is included if that plan does not exist.
    IDCW (dividend) plans are refused for return analytics with an explicit error — AMFI
    NAV for IDCW plans is not distribution-adjusted, so computing CAGR from it would
    silently understate returns. Request a Growth plan instead.
    risk_free_annual: overrides the stated-constant risk-free rate used for Sharpe/Sortino/
    alpha (default in config.DEFAULT_RISK_FREE_RATE_ANNUAL). Sharpe is only comparable
    across funds computed with the SAME rate — pin this explicitly for any comparison that
    spans a period where the real rate moved.
    Never emits a score, rating, or recommendation — only computed facts with provenance.
    """
    with connect() as conn:
        return _get_fund_performance(
            conn, scheme_ids, plan=plan, period=period, comparators=comparators,
            metrics=metrics, rolling_windows=rolling_windows, nav_series=nav_series,
            provenance=provenance, risk_free_annual=risk_free_annual,
        )


@mcp.tool()
def get_fund_portfolio(
    scheme_ids: list[str],
    as_of: str = "latest",
    compare_to: str | None = "prev_month",
    history: str = "none",
    sections: list[str] | None = None,
    holdings_limit: int | None = None,
    provenance: str = "compact",
) -> dict:
    """Holdings, allocations, and month-over-month change detection for one or more schemes.

    sections: any of "holdings","allocations","changes","concentration","persistence","overlap".
    history: "none"|"12M"|"36M"|"60M" — required (non-"none") for the persistence table.
    compare_to: a date, "prev_month", or null to skip change detection.
    Corporate actions (splits/bonuses/mergers) are flagged, never silently asserted as trades.
    Market-cap allocation (within "allocations") is point-in-time-safe against AMFI's
    half-yearly cap list, version-stamped so history is never reclassified with a newer list —
    but requires `mf-mcp update-caplist` to have populated that join; until then it's reported
    as unavailable rather than guessed. "overlap" (only meaningful with 2+ scheme_ids) returns
    pairwise ISIN-set intersection weighted by %NAV. Coverage depends on which AMC adapters
    have been run.
    """
    with connect() as conn:
        return _get_fund_portfolio(
            conn, scheme_ids, as_of=as_of, compare_to=compare_to, history=history,
            sections=sections, holdings_limit=holdings_limit, provenance=provenance,
        )


@mcp.tool()
def get_fund_profile(
    scheme_ids: list[str],
    as_of: str | None = None,
    sections: list[str] | None = None,
    provenance: str = "compact",
) -> dict:
    """Static/slow-moving profile: identity, mandate excerpts, benchmark, costs, managers, documents.

    sections: any of "identity","mandate","benchmark","costs","managers","documents".
    Mandate is always returned as verbatim SID excerpts with page numbers, never as parsed
    fields — investment philosophy/strategy is a document-retrieval problem, not structured
    data (extracting it would produce confident nonsense). Managers and TER are populated
    from ingested factsheets (`mf-mcp ingest-factsheet` / `backfill-factsheets`); until a
    scheme's factsheets have been ingested, these report as unavailable, never fabricated.
    """
    with connect() as conn:
        return _get_fund_profile(conn, scheme_ids, as_of=as_of, sections=sections, provenance=provenance)


@mcp.tool()
def get_document(
    doc_id: str | None = None,
    scheme_id: str | None = None,
    doc_type: str | None = None,
    as_of: str | None = None,
    query: str | None = None,
    sections: list[str] | None = None,
    max_chars: int = 15000,
    return_: str = "text",
) -> dict:
    """Retrieve source-document evidence — the relevant pages, not the whole PDF.

    Identify the document either by `doc_id` (from get_fund_profile's documents list) or by
    `scheme_id` + `doc_type` (+ optional `as_of`). Use `query` for keyword-scoped retrieval or
    `sections` (SEBI-standard headings: Investment Objective, Investment Strategy, Asset
    Allocation Pattern, Risk Factors, Fund Manager, Load Structure, Expenses of the Scheme).
    Returns verbatim text with page numbers, sha256, and the source URL — Claude reads and
    interprets this prose; the server never paraphrases or extracts it into structured claims.
    """
    with connect() as conn:
        return _get_document(
            conn, doc_id=doc_id, scheme_id=scheme_id, doc_type=doc_type, as_of=as_of,
            query=query, sections=sections, max_chars=max_chars, return_=return_,
        )


@mcp.tool()
def list_disclosure_events(
    scheme_ids: list[str],
    event_types: list[str] | None = None,
    since: str | None = None,
    provenance: str = "compact",
) -> dict:
    """Detected disclosure change events: manager changes, TER changes, benchmark changes, etc.

    Returns ChangeEvents recorded during factsheet ingestion, newest-first.
    Also includes a manager_timeline summary for each scheme.

    event_types: any of "manager_change","ter_change","benchmark_change",
                 "category_change","mandate_revision","addendum". Default: all.
    since: ISO date string — only events detected on/after this date.

    Events with confidence='observed' are derived by diffing consecutive factsheets —
    the effective_date may differ from detected_date, and the exact change is inferred.
    Only events with confidence='official' (addendum-sourced) are legally authoritative.
    Requires factsheets to have been ingested via `mf-mcp ingest-factsheet` or
    `mf-mcp backfill-factsheets`.
    """
    with connect() as conn:
        return _list_disclosure_events(
            conn, scheme_ids, event_types=event_types, since=since, provenance=provenance,
        )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
