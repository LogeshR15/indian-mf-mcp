"""Full-stack automation: drive the real MCP server over the actual protocol layer.

Unlike the other integration tests (which call the tool functions directly), this
suite goes through `FastMCP`'s `call_tool` machinery via an in-memory client session —
the same code path a real MCP client (Claude, an IDE, etc.) exercises: JSON-RPC
request -> tool dispatch -> `connect()` -> tool function -> JSON-serialisable result.

It seeds one shared synthetic universe (3 schemes, multiple plans/options, ~5 years of
daily NAVs) into a temp SQLite store and points `config.DB_PATH` at it, then runs a wide
edge-case matrix across all six tools: happy path, ambiguous/blank/unicode input, unknown
scheme_ids, empty lists, invalid enum values, boundary limits, and multi-scheme comparison
calls. Every assertion is either "the protocol call didn't blow up and returned well-formed
JSON" or a specific behavioural contract (e.g. "an error surfaces as `error`, never as a
fabricated value").
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from indian_mf_mcp import config
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _seed_universe(db_path):
    conn = get_connection(db_path)
    repo.upsert_amc(conn, "amc-ppfas", "PPFAS Mutual Fund")
    repo.upsert_amc(conn, "amc-hdfc", "HDFC Mutual Fund")

    def _seed_scheme(scheme_id, amc_id, name, sub_category, plans, growth_rate):
        repo.upsert_scheme(conn, scheme_id, amc_id, name, "Open Ended Schemes",
                            "Equity Scheme", sub_category, "2016-01-01")
        repo.insert_taxonomy_history(conn, scheme_id, "2016-01-01", "Open Ended Schemes",
                                      "Equity Scheme", sub_category,
                                      f"Open Ended Schemes(Equity Scheme - {sub_category})")
        for plan_id, code, isin, plan_type, option_type, idcw in plans:
            repo.upsert_plan(conn, plan_id, scheme_id, code, isin, plan_type, option_type,
                              idcw, "2016-01-01")
        start = date(2016, 1, 1)
        nav = 100.0
        rows = []
        d = start
        for _ in range(2000):
            for plan_id, *_ in plans:
                rows.append((plan_id, d.isoformat(), round(nav, 4)))
            nav *= (1 + growth_rate)
            d += timedelta(days=1)
        repo.insert_nav_points(conn, rows)

    _seed_scheme(
        "scheme-alpha", "amc-ppfas", "Automation Test Flexi Cap Fund", "Flexi Cap Fund",
        [
            ("plan-alpha-dg", "900001", "INF900A01DG1", "Direct", "Growth", None),
            ("plan-alpha-rg", "900002", "INF900A01RG1", "Regular", "Growth", None),
        ],
        growth_rate=0.0004,
    )
    _seed_scheme(
        "scheme-beta", "amc-hdfc", "Automation Test Large Cap Fund", "Large Cap Fund",
        [("plan-beta-dg", "900003", "INF900B01DG1", "Direct", "Growth", None)],
        growth_rate=0.0003,
    )
    # A scheme with a single, non-Direct/Growth plan only — exercises the fallback path.
    _seed_scheme(
        "scheme-gamma", "amc-hdfc", "Automation Test IDCW-Only Fund", "Value Fund",
        [("plan-gamma-ri", "900004", "INF900C01RI1", "Regular", "IDCW", "Payout")],
        growth_rate=0.0002,
    )
    conn.commit()
    conn.close()


@pytest.fixture(scope="session")
def seeded_db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("mcp_automation") / "store.db"
    _seed_universe(path)
    return path


@pytest.fixture(autouse=True)
def _point_at_seeded_db(seeded_db_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", seeded_db_path)


@pytest.fixture
async def session():
    from mcp.shared.memory import create_connected_server_and_client_session
    from indian_mf_mcp import server as server_mod

    async with create_connected_server_and_client_session(server_mod.mcp) as s:
        yield s


def _payload(result):
    """Unwrap a CallToolResult's single text content block into a Python object."""
    assert not result.isError, f"tool call reported isError: {result.content}"
    assert len(result.content) == 1
    return json.loads(result.content[0].text)


# ---------------------------------------------------------------------------
# resolve_fund
# ---------------------------------------------------------------------------

async def test_resolve_fund_happy_path(session):
    out = _payload(await session.call_tool("resolve_fund", {"query": "Automation Test Flexi Cap Fund"}))
    candidates = out["Automation Test Flexi Cap Fund"]
    assert len(candidates) == 1
    assert candidates[0]["scheme_id"] == "scheme-alpha"
    assert len(candidates[0]["plans"]) == 2


async def test_resolve_fund_by_isin(session):
    out = _payload(await session.call_tool("resolve_fund", {"query": "INF900A01DG1"}))
    assert out["INF900A01DG1"][0]["scheme_id"] == "scheme-alpha"


async def test_resolve_fund_batch_query(session):
    out = _payload(await session.call_tool("resolve_fund", {
        "query": ["Automation Test Flexi Cap Fund", "Automation Test Large Cap Fund", "Nonexistent XYZ"],
    }))
    assert out["Automation Test Flexi Cap Fund"][0]["scheme_id"] == "scheme-alpha"
    assert out["Automation Test Large Cap Fund"][0]["scheme_id"] == "scheme-beta"
    assert out["Nonexistent XYZ"] == []
    assert "_warnings" in out


async def test_resolve_fund_blank_query_matches_nothing(session):
    """Regression for the SQL LIKE '%%' bug: blank input must not match every fund."""
    for blank in ["", "   "]:
        out = _payload(await session.call_tool("resolve_fund", {"query": blank}))
        assert out[blank] == []


async def test_resolve_fund_unicode_and_long_input_do_not_crash(session):
    out = _payload(await session.call_tool("resolve_fund", {"query": "मुद्रा निधि 基金 " * 20}))
    assert isinstance(out, dict)


async def test_resolve_fund_limit_zero(session):
    out = _payload(await session.call_tool("resolve_fund", {"query": "Automation Test", "limit": 0}))
    assert out["Automation Test"] == []


async def test_resolve_fund_negative_limit_does_not_crash(session):
    result = await session.call_tool("resolve_fund", {"query": "Automation Test", "limit": -1})
    # Either a graceful empty/short result or a clean tool-level error — never a hard crash.
    if not result.isError:
        out = json.loads(result.content[0].text)
        assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# get_fund_performance
# ---------------------------------------------------------------------------

async def test_get_fund_performance_happy_path(session):
    out = _payload(await session.call_tool("get_fund_performance", {"scheme_ids": ["scheme-alpha"]}))
    data = out["scheme-alpha"]["data"]
    assert data["cagr_1y"]["v"] is not None
    assert data["cagr_1y"]["k"] == "calculated"
    for fact in data.values():
        assert fact["src"] in out["scheme-alpha"]["sources"]


async def test_get_fund_performance_multi_scheme_comparison(session):
    out = _payload(await session.call_tool("get_fund_performance", {
        "scheme_ids": ["scheme-alpha", "scheme-beta", "scheme-gamma"],
        "metrics": ["trailing"],
    }))
    assert set(out.keys()) == {"scheme-alpha", "scheme-beta", "scheme-gamma"}
    # scheme-gamma has no Growth plan at all (Regular/IDCW only) -> return analytics must
    # be refused outright, never silently computed off non-distribution-adjusted IDCW NAV.
    assert out["scheme-gamma"]["error"] == "idcw_plan_return_analytics_unsupported"
    assert out["scheme-gamma"]["meta"]["plan_id"] == "plan-gamma-ri"


async def test_get_fund_performance_idcw_plan_is_refused_not_understated(session):
    """Regression: an IDCW plan's NAV is not distribution-adjusted; computing CAGR off it
    would silently understate every return. Must error, never emit a plausible-looking
    but wrong figure."""
    out = _payload(await session.call_tool(
        "get_fund_performance", {"scheme_ids": ["scheme-gamma"], "plan": "regular_idcw"}
    ))
    assert out["scheme-gamma"]["error"] == "idcw_plan_return_analytics_unsupported"
    assert "data" not in out["scheme-gamma"]


async def test_get_fund_performance_unknown_scheme_id(session):
    out = _payload(await session.call_tool("get_fund_performance", {"scheme_ids": ["scheme-does-not-exist"]}))
    assert out["scheme-does-not-exist"]["error"] == "no_plans_found"


async def test_get_fund_performance_empty_scheme_list(session):
    out = _payload(await session.call_tool("get_fund_performance", {"scheme_ids": []}))
    assert out == {}


async def test_get_fund_performance_regular_plan_is_honoured(session):
    """Regression for the ignored-plan bug: regular_growth must diverge from direct_growth."""
    direct = _payload(await session.call_tool(
        "get_fund_performance", {"scheme_ids": ["scheme-alpha"], "plan": "direct_growth", "metrics": ["trailing"]}
    ))
    regular = _payload(await session.call_tool(
        "get_fund_performance", {"scheme_ids": ["scheme-alpha"], "plan": "regular_growth", "metrics": ["trailing"]}
    ))
    assert direct["scheme-alpha"]["meta"]["plan_id"] == "plan-alpha-dg"
    assert regular["scheme-alpha"]["meta"]["plan_id"] == "plan-alpha-rg"


async def test_get_fund_performance_unrecognised_plan_warns(session):
    out = _payload(await session.call_tool(
        "get_fund_performance", {"scheme_ids": ["scheme-alpha"], "plan": "gold_plan", "metrics": ["trailing"]}
    ))
    assert any("gold_plan" in w for w in out["scheme-alpha"]["meta"]["warnings"])


async def test_get_fund_performance_invalid_metrics_key_ignored_not_crashed(session):
    result = await session.call_tool(
        "get_fund_performance", {"scheme_ids": ["scheme-alpha"], "metrics": ["not_a_real_metric"]}
    )
    assert not result.isError
    out = json.loads(result.content[0].text)
    # No metric block requested is recognised -> no data facts beyond nothing computed, but must not error
    assert "scheme-alpha" in out


async def test_get_fund_performance_duplicate_scheme_ids(session):
    out = _payload(await session.call_tool(
        "get_fund_performance", {"scheme_ids": ["scheme-alpha", "scheme-alpha"], "metrics": ["trailing"]}
    ))
    assert list(out.keys()) == ["scheme-alpha"]


# ---------------------------------------------------------------------------
# get_fund_profile
# ---------------------------------------------------------------------------

async def test_get_fund_profile_happy_path(session):
    out = _payload(await session.call_tool("get_fund_profile", {"scheme_ids": ["scheme-alpha"]}))
    data = out["scheme-alpha"]["data"]
    assert data["name"]["v"] == "Automation Test Flexi Cap Fund"
    assert len(data["plans"]["v"]) == 2


async def test_get_fund_profile_unknown_scheme(session):
    result = await session.call_tool("get_fund_profile", {"scheme_ids": ["scheme-ghost"]})
    assert not result.isError
    out = json.loads(result.content[0].text)
    assert isinstance(out.get("scheme-ghost", {}), dict)


async def test_get_fund_profile_sections_filter(session):
    out = _payload(await session.call_tool(
        "get_fund_profile", {"scheme_ids": ["scheme-alpha"], "sections": ["managers"]}
    ))
    data = out["scheme-alpha"]["data"]
    assert "managers" in data
    assert "ter_direct" not in data


# ---------------------------------------------------------------------------
# get_fund_portfolio
# ---------------------------------------------------------------------------

async def test_get_fund_portfolio_no_data_reports_error_not_empty_success(session):
    out = _payload(await session.call_tool("get_fund_portfolio", {"scheme_ids": ["scheme-alpha"]}))
    assert out["scheme-alpha"]["error"] == "no_portfolio_data"


async def test_get_fund_portfolio_negative_holdings_limit_does_not_crash(session):
    result = await session.call_tool(
        "get_fund_portfolio", {"scheme_ids": ["scheme-alpha"], "holdings_limit": -5}
    )
    assert not result.isError


# ---------------------------------------------------------------------------
# list_disclosure_events
# ---------------------------------------------------------------------------

async def test_list_disclosure_events_no_data(session):
    out = _payload(await session.call_tool("list_disclosure_events", {"scheme_ids": ["scheme-alpha"]}))
    assert out["scheme-alpha"]["meta"]["n_events"] == 0
    assert "warnings" in out["scheme-alpha"]["meta"]


async def test_list_disclosure_events_invalid_event_type_does_not_crash(session):
    result = await session.call_tool(
        "list_disclosure_events", {"scheme_ids": ["scheme-alpha"], "event_types": ["not_a_real_type"]}
    )
    assert not result.isError


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------

async def test_get_document_not_found_is_explicit(session):
    out = _payload(await session.call_tool(
        "get_document", {"scheme_id": "scheme-alpha", "doc_type": "SID"}
    ))
    assert out["error"] == "document_not_found"


async def test_get_document_no_identifying_args_does_not_crash(session):
    result = await session.call_tool("get_document", {})
    assert not result.isError


# ---------------------------------------------------------------------------
# Cross-cutting: tool listing matches the six documented tools
# ---------------------------------------------------------------------------

async def test_all_six_tools_are_registered(session):
    listed = await session.list_tools()
    names = {t.name for t in listed.tools}
    assert names == {
        "resolve_fund", "get_fund_performance", "get_fund_profile",
        "get_fund_portfolio", "list_disclosure_events", "get_document",
    }


def _collect_fact_srcs(node, out):
    """Recursively find every `{"v", "src", "k"}`-shaped fact and collect its `src` key —
    the automated version of "never ship a bare number". Walks any dict/list nesting so
    it works unmodified across all six tools' payload shapes."""
    if isinstance(node, dict):
        if {"v", "src", "k"} <= node.keys():
            out.append(node["src"])
            return
        for value in node.values():
            _collect_fact_srcs(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_fact_srcs(value, out)


async def test_provenance_completeness_across_all_tools(session):
    """Every fact's `src` key must resolve into that scheme's `sources` table, for every
    tool that emits a provenance envelope. Automates the "shipped a bare number" class of
    regression instead of relying on spot-checks in individual tests."""
    calls = [
        ("get_fund_performance", {"scheme_ids": ["scheme-alpha", "scheme-beta"]}),
        ("get_fund_profile", {"scheme_ids": ["scheme-alpha", "scheme-beta"]}),
    ]
    for tool_name, args in calls:
        out = _payload(await session.call_tool(tool_name, args))
        for scheme_id, payload in out.items():
            if "data" not in payload:
                continue  # error payloads (e.g. no_plans_found) carry no provenance to check
            srcs = []
            _collect_fact_srcs(payload["data"], srcs)
            sources = payload.get("sources", {})
            for src in srcs:
                assert src in sources, (
                    f"{tool_name}[{scheme_id}]: fact references src={src!r} "
                    f"which is not in sources={sorted(sources)}"
                )
