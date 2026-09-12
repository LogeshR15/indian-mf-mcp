"""Live-network smoke test for the DSP adapter (spec §9: manually triggered, skips if network
unavailable). Discovery: DSP's own sitemap.xml maps each scheme's product-page slug to a
short code, which resolves via `/mandatory-disclosures/scheme-portfolio/<code>` (a 302
redirect to the current month's actual holdings file) — the file found on the main disclosure
page is a returns/AUM summary, not this one.

Note: this endpoint only ever serves the LATEST disclosure (no historical-month parameter
discoverable) — a real capability limit, so `since` only affects whether the current month
qualifies, not how many months are returned.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.dsp import DSPAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-dsp-flexicap-test"
AMC_ID = "amc-dsp"
SCHEME_HINT = "DSP Flexi Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.dspim.com/", timeout=10)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_dsp_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "dsp_live.db")
    repo.upsert_amc(conn, AMC_ID, "DSP Asset Managers Private Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = DSPAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2020, 1, 1),
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 50
    icici = next((h for h in data["holdings"]["v"] if "ICICI Bank" in h["instrument_name"]), None)
    assert icici is not None
    assert icici["isin"] == "INE090A01021"

    conn.close()
