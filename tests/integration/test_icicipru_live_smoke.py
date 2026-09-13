"""Live-network smoke test for the ICICI Prudential Mutual Fund adapter (spec §9: manually
triggered, skips if network unavailable). Discovery needed no API/Playwright reverse-
engineering to run per-request — the actual document host is a computable Azure Blob Storage
URL (`www.icicipruamc.com/blob/downloads/Files/Monthly Portfolio Disclosures/<YYYY>/<Mon>/
Monthly-Portfolio-Disclosure-<Month>-<YYYY>.zip`), found via a one-time browser capture of the
real "Download" button's `window.open()` call (the listing SPA's own metadata API,
apimf.icicipruamc.com, is a 401'ing dead end and is never used).
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.icici_prudential import ICICIPrudentialAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-icicipru-value-fund-test"
AMC_ID = "amc-icici-prudential"
SCHEME_HINT = "ICICI Prudential Value Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.icicipruamc.com/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_icicipru_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "icicipru_live.db")
    repo.upsert_amc(conn, AMC_ID, "ICICI Prudential Asset Management Company Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Value Fund", date.today().isoformat())
    conn.commit()

    adapter = ICICIPrudentialAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2026, 1, 1),
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 30
    icici_bank = next((h for h in data["holdings"]["v"] if "ICICI Bank" in h["instrument_name"]), None)
    assert icici_bank is not None
    assert icici_bank["isin"] == "INE090A01021"

    conn.close()
