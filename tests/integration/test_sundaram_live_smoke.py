"""Live-network smoke test for the Sundaram adapter (spec §9: manually triggered, skips if
network unavailable). Discovery via a legacy ASP.NET AJAX (.ashx) endpoint found through a
one-time Playwright network capture; combined one-workbook-per-month layout with an
"ACRONYM"-keyed Index sheet and a "% of Net Asset" header wording, both of which required
generalizing the shared parser/resolver rather than special-casing this adapter alone.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.ingest.amc_adapters.sundaram import SundaramAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-sundaram-largemidcap-test"
AMC_ID = "amc-sundaram"
SCHEME_HINT = "Sundaram Large And Mid Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.sundarammutual.com/", timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_sundaram_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "sundaram_live.db")
    repo.upsert_amc(conn, AMC_ID, "Sundaram Asset Management Company Ltd")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Large & Mid Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = SundaramAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2026, 6, 1),
        sheet_resolver=find_sheet_code,
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_sheet_not_found"] == 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 20
    radico = next((h for h in data["holdings"]["v"] if "Radico Khaitan" in h["instrument_name"]), None)
    assert radico is not None
    assert radico["isin"] == "INE944F01028"

    conn.close()
