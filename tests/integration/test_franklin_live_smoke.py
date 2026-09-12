"""Live-network smoke test for the Franklin Templeton adapter (spec §9: manually triggered,
skips if network unavailable). Discovery via api/literature/v1/responseLitJson + a
"download"-prefixed literatureHref, found via a one-time network capture + JS bundle
inspection. Combined workbook, sheet-by-title resolution (no Index sheet at all).
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_by_title
from indian_mf_mcp.ingest.amc_adapters.franklin_templeton import FranklinTempletonAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-franklin-largecap-test"
AMC_ID = "amc-franklin-templeton"
SCHEME_HINT = "Franklin India Large Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.franklintempletonindia.com/", timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_franklin_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "franklin_live.db")
    repo.upsert_amc(conn, AMC_ID, "Franklin Templeton Asset Management (India) Private Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Large Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = FranklinTempletonAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2026, 6, 1),
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
