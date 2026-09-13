"""Live-network smoke test for the Trust Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery needed no browser at all: the site's own served JS
bundle names a generic `Trust/GetData` POST endpoint (found via its runtime /config.json), and
one call to it returns the entire monthly-portfolio archive back to February 2021.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.trust import TrustAdapter, _resolve_sheet
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-trust-flexicap-test"
AMC_ID = "amc-trust"
SCHEME_HINT = "TRUSTMF Flexi Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.trustmf.com/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_trust_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "trust_live.db")
    repo.upsert_amc(conn, AMC_ID, "Trust Asset Management Private Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = TrustAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2026, 3, 1),
        sheet_resolver=_resolve_sheet,
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
