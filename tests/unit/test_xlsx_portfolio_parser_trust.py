"""Golden test against a real, live-fetched Trust Mutual Fund combined workbook (August 2026).
This month's file names each sheet with a terse internal acronym (e.g. "TMFFLEXI") rather
than the scheme name, so the fixture is exercised through this adapter's own local
`_resolve_sheet` (no shared "Index" lookup sheet exists for this AMC) before parsing.
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.trust import _resolve_sheet
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "trust_all_aug2026.xlsx"


def _raw() -> bytes:
    return FIXTURE.read_bytes()


def test_sheet_resolver_finds_correct_acronym_sheet():
    raw = _raw()
    assert _resolve_sheet(raw, "TRUSTMF Flexi Cap Fund") == "TMFFLEXI"
    assert _resolve_sheet(raw, "TRUSTMF Liquid Fund") == "TMFLIQ"


def test_sheet_resolver_unknown_scheme_returns_none():
    raw = _raw()
    assert _resolve_sheet(raw, "Totally Unknown Fund XYZ") is None


def test_reconciliation_passes():
    raw = _raw()
    r = parse_portfolio_xlsx(raw, sheet_name="TMFFLEXI")
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    raw = _raw()
    r = parse_portfolio_xlsx(raw, sheet_name="TMFFLEXI")
    assert len(r.holdings) > 50
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici.isin == "INE090A01021"
    assert icici.asset_class == "equity"


def test_percentage_already_fractional():
    raw = _raw()
    r = parse_portfolio_xlsx(raw, sheet_name="TMFFLEXI")
    icici = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert abs(icici.pct_nav - 0.0583) < 1e-6
