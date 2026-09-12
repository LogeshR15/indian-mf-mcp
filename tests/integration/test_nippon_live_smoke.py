"""Live-network smoke test for the Nippon India adapter (spec §9: manually triggered, skips
if network unavailable). Discovery is a plain server-rendered HTML page (no Playwright/API
needed) — the one AMC so far where file links are already static despite the site otherwise
being SharePoint-backed.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.ingest.amc_adapters.nippon import NipponAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-nippon-consumption-test"
AMC_ID = "amc-nippon-india"
SCHEME_HINT = "Nippon India Consumption Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://mf.nipponindiaim.com/", timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_nippon_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "nippon_live.db")
    repo.upsert_amc(conn, AMC_ID, "Nippon Life India Asset Management Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Thematic Fund", date.today().isoformat())
    conn.commit()

    adapter = NipponAdapter()
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
    mm = next((h for h in data["holdings"]["v"] if "Mahindra" in h["instrument_name"]), None)
    assert mm is not None
    assert mm["isin"] == "INE101A01026"

    conn.close()
