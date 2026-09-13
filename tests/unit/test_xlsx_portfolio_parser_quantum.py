"""Golden test against a real, live-fetched Quantum Mutual Fund combined workbook
(August 2026), covering all 15 live schemes in one file. Quantum's Index sheet header reads
"Scheme Full Name" / "Scheme Code" (not the "scheme name"/"fund name" substrings the shared
`combined_workbook.find_sheet_code` looks for), so the scheme's own sheet is selected via the
local `_resolve_sheet` helper in `indian_mf_mcp.ingest.amc_adapters.quantum`, then parsed with
the shared, unmodified `xlsx_portfolio.parse_portfolio_xlsx`."""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.quantum import _resolve_sheet
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "quantum_valuefund_aug2026.xlsx"


def _parse():
    raw = FIXTURE.read_bytes()
    sheet_name = _resolve_sheet(raw, "Quantum Value Fund")
    assert sheet_name == "QLTEVF"
    return parse_portfolio_xlsx(raw, sheet_name=sheet_name)


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 10
    hdfc_bank = next(h for h in r.holdings if "HDFC Bank" in h.instrument_name)
    assert hdfc_bank.isin == "INE040A01034"
    assert hdfc_bank.asset_class == "equity"


def test_specific_holding_pct_nav():
    r = _parse()
    hdfc_bank = next(h for h in r.holdings if "HDFC Bank" in h.instrument_name)
    assert abs(hdfc_bank.pct_nav - 0.0543) < 1e-4
