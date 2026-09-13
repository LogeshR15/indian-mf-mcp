"""Live-network smoke test for the Bank of India Mutual Fund adapter (spec §9: manually
triggered, skips if network unavailable). Discovery via the site's own bundled AjaxCall.js
handler, reproduced as a single plain httpx POST to /AjaxService.asmx/GetDocuments (no
Playwright/browser needed at any point — the handler source itself is a public static JS
asset). Combined one-workbook-per-month layout with a plain "Scheme Code"/"Scheme Names"
Index sheet, resolved via the shared combined_workbook.find_sheet_code with zero changes.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.bank_of_india import BankOfIndiaAdapter
from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_code
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-boi-flexicap-test"
AMC_ID = "amc-bank-of-india"
SCHEME_HINT = "Bank of India Flexi Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://www.boimf.in/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_boi_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "boi_live.db")
    repo.upsert_amc(conn, AMC_ID, "Bank of India Investment Managers Private Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = BankOfIndiaAdapter()
    stats = ingest_scheme_portfolios(
        conn, adapter, SCHEME_ID, SCHEME_HINT, since=date(2025, 6, 1),
        sheet_resolver=find_sheet_code,
    )
    conn.commit()

    assert stats["documents_found"] > 0
    assert stats["ingested"] > 0
    assert stats["skipped_sheet_not_found"] == 0
    assert stats["skipped_reconciliation_failed"] == 0

    out = get_fund_portfolio(conn, [SCHEME_ID], sections=["holdings", "concentration"])
    data = out[SCHEME_ID]["data"]
    assert len(data["holdings"]["v"]) > 20
    sbi = next((h for h in data["holdings"]["v"] if "State Bank of India" in h["instrument_name"]), None)
    assert sbi is not None
    assert sbi["isin"] == "INE062A01020"

    conn.close()
