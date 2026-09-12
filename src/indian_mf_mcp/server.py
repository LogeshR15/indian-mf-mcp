"""MCP server entrypoint. Phase 1: resolve_fund + get_fund_performance only."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from indian_mf_mcp.store.db import connect
from indian_mf_mcp.tools.get_document import get_document as _get_document
from indian_mf_mcp.tools.get_fund_performance import get_fund_performance as _get_fund_performance
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio as _get_fund_portfolio
from indian_mf_mcp.tools.get_fund_profile import get_fund_profile as _get_fund_profile
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
) -> dict:
    """Complete return/risk evidence pack for one or more schemes (list = comparison).

    metrics: any of "trailing","rolling","risk","drawdown","stress".
    comparators: any of "benchmark" (not yet implemented — proxy pending), "category".
    Defaults to the Direct/Growth plan; a warning is included if that plan does not exist.
    Never emits a score, rating, or recommendation — only computed facts with provenance.
    """
    with connect() as conn:
        return _get_fund_performance(
            conn, scheme_ids, plan=plan, period=period, comparators=comparators,
            metrics=metrics, rolling_windows=rolling_windows, nav_series=nav_series,
            provenance=provenance,
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

    sections: any of "holdings","allocations","changes","concentration","persistence".
    history: "none"|"12M"|"36M"|"60M" — required (non-"none") for the persistence table.
    compare_to: a date, "prev_month", or null to skip change detection.
    Corporate actions (splits/bonuses/mergers) are flagged, never silently asserted as trades.
    Market-cap allocation is not yet available (requires the AMFI cap-list join) and is
    reported as null rather than guessed. Coverage depends on which AMC adapters have been run.
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
    data (extracting it would produce confident nonsense). Managers and TER are not yet
    available in this build and are reported as unavailable, never fabricated.
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
