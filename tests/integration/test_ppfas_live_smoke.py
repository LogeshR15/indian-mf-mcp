"""Live-network smoke test (spec §9 testing strategy: manually triggered, not part of the
always-on unit/integration suite that runs offline against fixtures). Proves the Phase-2
end-to-end thesis: PPFAS adapter -> parse -> store -> get_fund_portfolio, against the real
AMC site.

Skips (rather than fails) if the network is unavailable, since CI environments may block it.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.ppfas import PPFASAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-ppfas-flexicap-test"
AMC_ID = "amc-ppfas"


def _network_available() -> bool:
    try:
        httpx.get("https://amc.ppfas.com/", timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_ppfas_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "ppfas_live.db")
    repo.upsert_amc(conn, AMC_ID, "PPFAS Asset Management Private Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, "Parag Parikh Flexi Cap Fund",
                        "Open Ended Schemes", "Equity Scheme", "Flexi Cap Fund",
                        date.today().isoformat())
    conn.commit()

    adapter = PPFASAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, "Parag Parikh Flexi Cap Fund", since=date(2025, 1, 1),
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(
        conn, [SCHEME_ID], as_of="latest", compare_to="prev_month", history="12M",
        sections=["holdings", "allocations", "concentration", "changes", "persistence"],
    )
    payload = out[SCHEME_ID]
    data = payload["data"]

    assert len(data["holdings"]["v"]) > 50
    assert data["concentration"]["v"]["top10_pct"] > 0
    assert data["allocations"]["v"]["market_cap"] is None  # honestly reported as unavailable
    assert "changes" in data or payload["meta"].get("warnings")
    assert "persistence" in data

    hdfc = next((h for h in data["holdings"]["v"] if h["instrument_name"] == "HDFC Bank Limited"), None)
    assert hdfc is not None
    assert hdfc["isin"] == "INE040A01034"

    conn.close()
