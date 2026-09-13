"""Golden test against a real, live-fetched Groww Mutual Fund combined workbook (August 2026).
Same shape as Franklin Templeton: no separate Index lookup sheet, sheet name is a short
legacy code ("IB01" etc, from the pre-rebrand Indiabulls Mutual Fund name), the scheme's full
name lives only in that sheet's own row 1 as "<code>-<scheme name>" — resolved via
find_sheet_by_title, whose substring match tolerates the leading code prefix.
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.combined_workbook import find_sheet_by_title
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "groww_largecap_aug2026.xlsx"


def test_sheet_resolver_finds_correct_code_by_title():
    raw = FIXTURE.read_bytes()
    assert find_sheet_by_title(raw, "Groww Large Cap Fund") == "IB01"
    assert find_sheet_by_title(raw, "Groww Liquid Fund") == "IB02"


def test_sheet_resolver_unknown_scheme_returns_none():
    raw = FIXTURE.read_bytes()
    assert find_sheet_by_title(raw, "Totally Unknown Fund XYZ") is None


def test_reconciliation_passes():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="IB01")
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw, sheet_name="IB01")
    assert len(r.holdings) > 30
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici.isin == "INE090A01021"
    assert abs(icici.pct_nav - 0.09229720711606376) < 1e-9
    assert icici.asset_class == "equity"
