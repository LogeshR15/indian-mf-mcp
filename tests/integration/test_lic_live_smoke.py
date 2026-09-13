"""Live-network smoke test for the LIC Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery drives a 4-step AJAX form chain
(category -> scheme code -> years -> months -> file link), documented in lic.py, reproduced
with plain httpx POSTs (no Playwright/browser at runtime)."""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.lic import LicAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-lic-flexicap-test"
AMC_ID = "amc-lic"
SCHEME_HINT = "LIC MF Flexi Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.licmf.com/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_lic_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "lic_live.db")
    repo.upsert_amc(conn, AMC_ID, "LIC Mutual Fund Asset Management Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = LicAdapter()
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
    icici = next((h for h in data["holdings"]["v"] if "ICICI Bank" in h["instrument_name"]), None)
    assert icici is not None
    assert icici["isin"] == "INE090A01021"

    conn.close()
