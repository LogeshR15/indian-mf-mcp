"""Live-network smoke test for the ITI Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery needs one encrypted POST to ITI's own
`jeeth/api/v1/catalog/getPartnerDocumentByType` endpoint (AES-128-CBC with a key/IV shipped
in ITI's own Angular bundle — see iti.py's module docstring) which returns the full monthly
archive back to 2019 in a single call; files themselves are plain honest-UA GETs on the same
host, no separate CDN.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.ingest.amc_adapters.iti import ITIAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-iti-flexicap-test"
AMC_ID = "amc-iti"
SCHEME_HINT = "ITI Flexi Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://itiamc.com/", headers={"User-Agent": config.USER_AGENT}, timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_iti_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "iti_live.db")
    repo.upsert_amc(conn, AMC_ID, "ITI Asset Management Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = ITIAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2025, 6, 1),
        sheet_resolver=find_sheet_code,
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
