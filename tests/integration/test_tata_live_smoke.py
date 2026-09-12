"""Live-network smoke test for the Tata adapter (spec §9: manually triggered, skips if
network unavailable). Sixth AMC — discovery via regex-scanning the Next.js page's own
server-rendered response (no separate API call needed); combined one-workbook-per-month
layout like SBI/Motilal Oswal.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.ingest.amc_adapters.tata import TataAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-tata-digitalindia-test"
AMC_ID = "amc-tata"
SCHEME_HINT = "Tata Digital India Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.tatamutualfund.com/", timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_tata_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "tata_live.db")
    repo.upsert_amc(conn, AMC_ID, "Tata Asset Management Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Sectoral/Thematic Fund", date.today().isoformat())
    conn.commit()

    adapter = TataAdapter()
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
    infosys = next((h for h in data["holdings"]["v"] if h["instrument_name"] == "INFOSYS LTD"), None)
    assert infosys is not None
    assert infosys["isin"] == "INE009A01021"

    conn.close()
