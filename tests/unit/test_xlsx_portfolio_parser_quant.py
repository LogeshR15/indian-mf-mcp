"""Golden test against a real, live-fetched quant Mutual Fund combined workbook (August 2026)
covering all ~28 schemes in one file. quant has no "Index" lookup sheet — the sheet holding
"quant Flexi Cap Fund" is selected via the local `_resolve_sheet` helper in
`indian_mf_mcp.ingest.amc_adapters.quant` (scheme name lives in each sheet's own row 2, not
row 1), then parsed with the shared, unmodified `xlsx_portfolio.parse_portfolio_xlsx`."""
from pathlib import Path

import pytest

from indian_mf_mcp.ingest.amc_adapters.quant import _resolve_sheet
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "quant_flexicap_aug2026.xlsx"


def _parse():
    raw = FIXTURE.read_bytes()
    sheet_name = _resolve_sheet(raw, "quant Flexi Cap Fund")
    assert sheet_name == "qFLEXI"
    return parse_portfolio_xlsx(raw, sheet_name=sheet_name)


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 10
    motherson = next(h for h in r.holdings if "Motherson" in h.instrument_name)
    assert motherson.isin == "INE775A01035"


def test_specific_holding_pct_nav():
    r = _parse()
    motherson = next(h for h in r.holdings if "Motherson" in h.instrument_name)
    assert abs(motherson.pct_nav - 0.0963405) < 1e-4


def test_rows_after_derivatives_section_are_holdings_and_reconcile():
    """quant prints futures as main-table rows, then T-bills / TREPS / net current assets.
    Before the fix those were swallowed as derivatives and only ~72% of NAV was extracted."""
    r = _parse()
    names = [h.instrument_name for h in r.holdings]
    assert any("Treasury Bill" in n for n in names)
    assert any("TREPS" in n for n in names)
    held = sum(h.pct_nav for h in r.holdings if h.pct_nav is not None)
    futures = sum(d.pct_to_aum for d in r.derivatives if d.pct_to_aum is not None)
    assert held + futures == pytest.approx(1.0, abs=0.005)
    assert all(abs(d.pct_to_aum) < 0.2 for d in r.derivatives if d.pct_to_aum is not None)


def test_sector_read_from_industry_not_rating_column():
    r = _parse()
    motherson = next(h for h in r.holdings if "Motherson" in h.instrument_name)
    assert motherson.industry_or_rating == "Auto Components"
