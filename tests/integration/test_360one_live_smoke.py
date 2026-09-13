"""Live-network smoke test for the 360 ONE Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery needed no AJAX/Playwright at all -- the entire
Monthly Portfolio archive (2018-2026) is embedded as a JSON payload inside the downloads
page's own React-Server-Components streaming <script> tags, read out of one page load.
Combined workbook (one file per month covering every scheme), sheet-by-title resolution
(no Index sheet at all), same shape as Franklin Templeton.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_by_title
from indian_mf_mcp.ingest.amc_adapters.three_sixty_one import ThreeSixtyOneAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-360one-flexicap-test"
AMC_ID = "amc-360-one"
SCHEME_HINT = "360 ONE Flexicap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.360.one/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_360one_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "360one_live.db")
    repo.upsert_amc(conn, AMC_ID, "360 ONE Asset Management Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = ThreeSixtyOneAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2025, 6, 1),
        sheet_resolver=find_sheet_by_title,
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_sheet_not_found"] == 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 20

    conn.close()
