"""Live-network smoke test for the Navi Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery needs one GET (to scrape the page's own WordPress
anonymous nonce) plus one POST per calendar month to the site's `nv/v1/documents` REST route —
no Playwright/browser was needed.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.navi import NaviAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-navi-flexicap-test"
AMC_ID = "amc-navi"
SCHEME_HINT = "Navi Flexi Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://navi.com/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_navi_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "navi_live.db")
    repo.upsert_amc(conn, AMC_ID, "Navi AMC Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = NaviAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2025, 6, 1),
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 30
    icici = next((h for h in data["holdings"]["v"] if "ICICI BANK" in h["instrument_name"].upper()), None)
    assert icici is not None
    assert icici["isin"] == "INE090A01021"

    conn.close()
