"""Live-network smoke test for the Groww Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery needed no API/Playwright at all — the entire
statutory-disclosure archive back to March 2023 is embedded in the disclosure page's own
`__NEXT_DATA__` script tag in one page load. Each monthly file is a combined workbook (one
sheet per scheme, no Index sheet), resolved via combined_workbook.find_sheet_by_title.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_by_title
from indian_mf_mcp.ingest.amc_adapters.groww import GrowwAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-groww-largecap-test"
AMC_ID = "amc-groww"
SCHEME_HINT = "Groww Large Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://growwmf.in/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_groww_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "groww_live.db")
    repo.upsert_amc(conn, AMC_ID, "Groww Asset Management Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Large Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = GrowwAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2025, 6, 1),
        sheet_resolver=find_sheet_by_title,
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 30
    icici = next((h for h in data["holdings"]["v"] if "ICICI Bank" in h["instrument_name"]), None)
    assert icici is not None
    assert icici["isin"] == "INE090A01021"

    conn.close()
