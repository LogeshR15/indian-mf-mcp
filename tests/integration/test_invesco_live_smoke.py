"""Live-network smoke test for the Invesco Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery is one plain GET per (year, classification) on
`www.invescomutualfund.com/api/CompleteMonthlyHoldings` -- a same-origin JSON API that returns
every scheme in that category, with direct fetchable XLSX URLs per month, in one response. No
separate CDN/S3 host, no scheme-name probing, no WAF encountered anywhere on this host (see
invesco.py's module docstring for the full re-investigation of the prior "blocked" verdict).
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.invesco import InvescoAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-invesco-elss-test"
AMC_ID = "amc-invesco"
SCHEME_HINT = "Invesco India ELSS Tax Saver Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.invescomutualfund.com/", timeout=15)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _network_available(), reason="network unavailable")


def test_invesco_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "invesco_live.db")
    repo.upsert_amc(conn, AMC_ID, "Invesco Asset Management (India) Private Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "ELSS", date.today().isoformat())
    conn.commit()

    stats = ingest_scheme_portfolios(
        conn, InvescoAdapter(), SCHEME_ID, SCHEME_HINT, since=date(2026, 6, 1),
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
