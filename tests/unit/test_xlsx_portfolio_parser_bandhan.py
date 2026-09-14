"""Golden test against a real, live-fetched Bandhan Mutual Fund combined "Debt Fund Portfolio"
workbook (August 2026). Bandhan has no Index sheet at all on either of its two combined-
workbook families, and no fixed title row/column across eras, so it carries its own
`_resolve_sheet` (a generic first-title-candidate scan -- see bandhan.py's module docstring).
Only the Debt Fund workbook from January 2025 onward has ISIN-complete holdings; older files
and the separate Equity Hybrid Fund workbook use an ISIN-less summary layout that
xlsx_portfolio.py correctly refuses to parse (parse_confidence 0.0) -- a real content gap, not
a parser deficiency, and not something this fixture exercises. No shared-parser change was
needed for the August 2026 file used here.
"""
from pathlib import Path

from indian_mf_mcp.ingest.amc_adapters.bandhan import _resolve_sheet
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "bandhan_lowdurationfund_aug2026.xlsx"


def test_sheet_resolver_finds_correct_sheet_by_title():
    raw = FIXTURE.read_bytes()
    assert _resolve_sheet(raw, "Bandhan Low Duration Fund") == "Bandhan LDF"
    assert _resolve_sheet(raw, "Bandhan Banking and PSU Fund") == "Bandhan Banking & PSU"


def test_sheet_resolver_unknown_scheme_returns_none():
    raw = FIXTURE.read_bytes()
    assert _resolve_sheet(raw, "Totally Unknown Fund XYZ") is None


def test_reconciliation_passes():
    raw = FIXTURE.read_bytes()
    sheet = _resolve_sheet(raw, "Bandhan Low Duration Fund")
    r = parse_portfolio_xlsx(raw, sheet_name=sheet)
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.005


def test_holdings_extracted():
    raw = FIXTURE.read_bytes()
    sheet = _resolve_sheet(raw, "Bandhan Low Duration Fund")
    r = parse_portfolio_xlsx(raw, sheet_name=sheet)
    assert len(r.holdings) > 30
    assert all(h.instrument_name for h in r.holdings)


def test_specific_holding_isin_and_pct_nav():
    raw = FIXTURE.read_bytes()
    sheet = _resolve_sheet(raw, "Bandhan Low Duration Fund")
    r = parse_portfolio_xlsx(raw, sheet_name=sheet)
    pfc = next(h for h in r.holdings if "Power Finance Corporation" in h.instrument_name)
    assert pfc.isin == "INE134E08NP7"
    assert abs(pfc.pct_nav - 0.0584) < 0.001
