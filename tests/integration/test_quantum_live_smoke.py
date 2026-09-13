"""Live-network smoke test for the Quantum Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery needed no API/Playwright reverse-engineering at all:
the AMFI-registered listing page (https://www.quantumamc.com/portfolio/combined/-1/1/0/0)
is plain server-rendered HTML whose download links are scraped from each anchor's own
`GTMcodeforxml(url, page, title, subtitle)` analytics call; history is reached by requesting
the same URL with a specific `{year}` path segment (`/portfolio/combined/-1/1/<year>/0`),
one GET per calendar year.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.quantum import QuantumAdapter, _resolve_sheet
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-quantum-valuefund-test"
AMC_ID = "amc-quantum"
SCHEME_HINT = "Quantum Value Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.quantumamc.com/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_quantum_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "quantum_live.db")
    repo.upsert_amc(conn, AMC_ID, "Quantum Asset Management Company Private Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Value Fund", date.today().isoformat())
    conn.commit()

    adapter = QuantumAdapter()
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
    assert len(data["holdings"]["v"]) > 10
    hdfc_bank = next((h for h in data["holdings"]["v"] if "HDFC Bank" in h["instrument_name"]), None)
    assert hdfc_bank is not None
    assert hdfc_bank["isin"] == "INE040A01034"

    conn.close()
