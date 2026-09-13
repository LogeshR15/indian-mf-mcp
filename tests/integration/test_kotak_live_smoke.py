"""Live-network smoke test for the Kotak Mahindra Mutual Fund adapter (spec §9: manually
triggered, skips if network unavailable).

Kotak was previously recorded as blocked outright ("Radware Bot Manager CAPTCHA"). That is
true of www.kotakmf.com itself (whole-domain Radware challenge, homepage included) but the
files are not blocked at all: vatseelabs-s3.kotakmf.com is a separate public CloudFront/S3
host with no such rule, and the monthly combined-portfolio-workbook URL is fully computable
from the as-of date (no scheme name, no listing page, no Playwright). See kotak.py's module
docstring for the full discovery method and the adapter-side column-repair this AMC's merged
header cells require.

No sheet_resolver is passed to ingest_scheme_portfolios here (unlike SBI/Tata, which are also
combined-workbook AMCs) because KotakAdapter.fetch() already extracts and repairs the single
scheme sheet internally, so the standard one-file-per-scheme pipeline applies unchanged.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.kotak import KotakAdapter
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-kotak-elss-test"
AMC_ID = "amc-kotak"
SCHEME_HINT = "Kotak ELSS Tax Saver Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://vatseelabs-s3.kotakmf.com/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_kotak_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "kotak_live.db")
    repo.upsert_amc(conn, AMC_ID, "Kotak Mahindra Asset Management Company Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "ELSS", date.today().isoformat())
    conn.commit()

    adapter = KotakAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2026, 5, 1),
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 30
    icici = next((h for h in data["holdings"]["v"] if "ICICI BANK" in h["instrument_name"].upper()), None)
    assert icici is not None
    assert icici["isin"] == "INE090A01021"

    conn.close()
