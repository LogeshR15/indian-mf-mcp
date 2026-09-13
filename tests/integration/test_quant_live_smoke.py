"""Live-network smoke test for the quant Mutual Fund adapter (spec §9: manually triggered,
skips if network unavailable). Discovery uses the ASP.NET WebForms PageMethod
(`statutorydisclosures.aspx/displaydisclouser`, POST JSON `{id: <year>, cat: "MONTHLY
PORTFOLIO"}`) found by ground-truthing https://quantmutual.com/statutory-disclosures live —
the same page the earlier BLOCKED verdict was (evidently, given this passes) not actually
testing, or was stale.
"""
from datetime import date

import httpx
import pytest

from indian_mf_mcp.ingest.amc_adapters.quant import QuantAdapter, _resolve_sheet
from indian_mf_mcp.ingest.portfolio_ingest import ingest_scheme_portfolios
from indian_mf_mcp.store import repository as repo
from indian_mf_mcp.store.db import get_connection
from indian_mf_mcp.tools.get_fund_portfolio import get_fund_portfolio

SCHEME_ID = "scheme-quant-flexicap-test"
AMC_ID = "amc-quant"
SCHEME_HINT = "quant Flexi Cap Fund"


def _network_available() -> bool:
    try:
        httpx.get("https://quantmutual.com/", timeout=15)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _network_available(), reason="network unavailable")
def test_quant_end_to_end_live(tmp_path):
    conn = get_connection(tmp_path / "quant_live.db")
    repo.upsert_amc(conn, AMC_ID, "quant Money Managers Limited")
    repo.upsert_scheme(conn, SCHEME_ID, AMC_ID, SCHEME_HINT, "Open Ended Schemes",
                        "Equity Scheme", "Flexi Cap Fund", date.today().isoformat())
    conn.commit()

    adapter = QuantAdapter()
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
    motherson = next((h for h in data["holdings"]["v"] if "Motherson" in h["instrument_name"]), None)
    assert motherson is not None
    assert motherson["isin"] == "INE775A01035"

    conn.close()
