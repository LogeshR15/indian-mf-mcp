"""Regression test: get_fund_profile's identity section must surface
is_segregated_portfolio_name() (spec §9.2) as an `inferred` fact, not silently drop it or
mislabel it `official` — AMFI's NAVAll.txt carries no dedicated field for this, so it must
never look as authoritative as the surrounding official scheme data.
"""
from __future__ import annotations

import sqlite3

from indian_mf_mcp.tools.get_fund_profile import get_fund_profile


def _db_with_scheme(name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open("src/indian_mf_mcp/store/schema.sql") as f:
        conn.executescript(f.read())
    conn.execute("INSERT INTO amc (amc_id, name) VALUES ('amc1', 'Franklin Templeton')")
    conn.execute(
        """INSERT INTO scheme (scheme_id, amc_id, name, category, sub_category,
             scheme_type, active) VALUES ('S1', 'amc1', ?, 'Debt', 'Ultra Short Duration',
             'open', 1)""",
        (name,),
    )
    conn.execute(
        """INSERT INTO plan (plan_id, scheme_id, amfi_scheme_code, plan_type, option_type)
           VALUES ('P1', 'S1', '12345', 'Direct', 'Growth')""",
    )
    conn.commit()
    return conn


def test_segregated_portfolio_scheme_flagged_as_inferred():
    conn = _db_with_scheme(
        "Franklin India Ultra Short Bond Fund - Segregated Portfolio 1"
    )
    result = get_fund_profile(conn, ["S1"], sections=["identity"])
    data = result["S1"]["data"]
    assert data["is_segregated_portfolio"]["v"] is True
    assert data["is_segregated_portfolio"]["k"] == "inferred"
    assert "not a normal standalone fund" in data["is_segregated_portfolio"]["caveat"]


def test_ordinary_scheme_has_no_segregated_portfolio_fact():
    conn = _db_with_scheme("Franklin India Ultra Short Bond Fund")
    result = get_fund_profile(conn, ["S1"], sections=["identity"])
    data = result["S1"]["data"]
    assert "is_segregated_portfolio" not in data
