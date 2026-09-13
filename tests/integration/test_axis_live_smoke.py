"""Live-network smoke test for the Axis Mutual Fund adapter (spec §9: manually triggered, skips
if network unavailable). Discovery needs no browser/Playwright at all — the disclosure-document
list is a plain unencrypted JSON API on www.axismf.com's own CMS group (see axis.py docstring
for the full ground-truthing: previously recorded as blocked, that verdict was about a browser
automation route that was never actually necessary).
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.axis import AxisAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-axis-midcap-test"
AMC_ID = "amc-axis"
SCHEME_HINT = "Axis Mid Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.axismf.com/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_axis_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "axis_live.db")
    repo.upsert_amc(conn, AMC_ID, "Axis Asset Management Company Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Mid Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = AxisAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2026, 6, 1),
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 30
    federal_bank = next(
        (h for h in data["holdings"]["v"] if "Federal Bank" in h["instrument_name"]), None
    )
    assert federal_bank is not None
    assert federal_bank["isin"] == "INE171A01029"

    conn.close()
