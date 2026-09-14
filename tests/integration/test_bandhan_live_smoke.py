"""Live-network smoke test for the Bandhan Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery is a single plain GET on `cmsnew.bandhanmutual.com`'s
custom `finance-api/v1` REST namespace -- unlike the CMS's own default `wp/v2` namespace
(locked down site-wide behind a 401 "DRA" plugin), this custom namespace has its own public
permission callback and needs no auth at all. See bandhan.py's module docstring for the full
discovery path and for why only the Debt Fund workbook from January 2025 onward reconciles
(the Equity Hybrid Fund workbook, and all pre-2025 Debt Fund files, use an ISIN-less summary
layout with no per-security detail -- a real content gap, not something this test's `since`
window exercises).
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.bandhan import BandhanAdapter, _resolve_sheet
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-bandhan-lowdurationfund-test"
AMC_ID = "amc-bandhan"
SCHEME_HINT = "Bandhan Low Duration Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://cmsnew.bandhanmutual.com/", headers={"User-Agent": config.USER_AGENT},
                   timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_bandhan_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "bandhan_live.db")
    repo.upsert_amc(conn, AMC_ID, "Bandhan AMC Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Debt Scheme", "Low Duration Fund", date.today().isoformat())
    conn.commit()

    adapter = BandhanAdapter()
    # Jan 2025 is when the Debt Fund workbook switched to an ISIN-complete layout; everything
    # before that is a real, documented coverage gap (see module docstring), so the window
    # here is chosen to land only on the reconciling era.
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2025, 6, 1),
        sheet_resolver=_resolve_sheet,
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 30

    conn.close()
