"""Live-network smoke test for the Baroda BNP Paribas adapter (spec §9: manually triggered,
skips if network unavailable). Discovered via AMFI's own registry JSON, which points
directly to a plain static-HTML disclosure page — no Playwright/API reverse-engineering
needed for this AMC, the simplest discovery of any AMC so far.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.baroda_bnp_paribas import BarodaBNPParibasAdapter
from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-barodabnp-largecap-test"
AMC_ID = "amc-baroda-bnp-paribas"
SCHEME_HINT = "Baroda BNP Paribas Large Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.barodabnpparibasmf.in/", timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_barodabnp_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "barodabnp_live.db")
    repo.upsert_amc(conn, AMC_ID, "Baroda BNP Paribas Asset Management India Private Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Large Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = BarodaBNPParibasAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2026, 1, 1),
        sheet_resolver=find_sheet_code,
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_sheet_not_found"] == 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 30
    icici = next((h for h in data["holdings"]["v"] if "ICICI Bank" in h["instrument_name"]), None)
    assert icici is not None
    assert icici["isin"] == "INE090A01021"

    conn.close()
