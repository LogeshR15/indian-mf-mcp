"""Live-network smoke test for the Union Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery needed no API/Playwright at all — the entire
archive back to 2021 is embedded as static inline-script JS literals in one page load.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.union import UnionAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-union-flexicap-test"
AMC_ID = "amc-union"
SCHEME_HINT = "Union Flexi Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.unionmf.com/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_union_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "union_live.db")
    repo.upsert_amc(conn, AMC_ID, "Union Asset Management Company Private Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = UnionAdapter()
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
