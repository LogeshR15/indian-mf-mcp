"""Live-network smoke test for the HDFC adapter (spec §9: manually triggered, skips if
network unavailable).

Discovery uses no listing page at all — `www.hdfcfund.com` 403s this project's honest
User-Agent — only computed S3 keys on `files.hdfcfund.com`, probed one cheap ranged GET per
candidate month.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.base import DocType
from indian_mf_mcp.ingest.amc_adapters.hdfc import HDFCAdapter, build_url
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-hdfc-flexicap-test"
AMC_ID = "amc-hdfc"
SCHEME_HINT = "HDFC Flexi Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://files.hdfcfund.com/", timeout=15)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _network_available(), reason="network unavailable")


def test_url_is_computed_not_scraped():
    """The whole point of this adapter: the key is derivable from (scheme, as_of) alone."""
    assert build_url("HDFC Flexi Cap Fund", date(2026, 8, 31)) == (
        "https://files.hdfcfund.com/s3fs-public/2026-09/"
        "Monthly%20HDFC%20Flexi%20Cap%20Fund%20-%2031%20August%202026.xlsx"
    )


def test_absent_month_is_skipped_not_fatal():
    """A missing key 403s (ListBucket is denied, so S3 says AccessDenied not NoSuchKey).
    That must read as 'no file', never as an access failure."""
    refs = HDFCAdapter().list_documents(
        DocType.MONTHLY_PORTFOLIO, date(2026, 7, 1), scheme_hint="HDFC Not A Real Fund",
    )
    assert refs == []


def test_hdfc_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "hdfc_live.db")
    repo.upsert_amc(conn, AMC_ID, "HDFC Asset Management Company Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    stats = ingest_scheme_portfolios(
        conn, HDFCAdapter(), SCHEME_ID, SCHEME_HINT, since=date(2026, 6, 1),
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
