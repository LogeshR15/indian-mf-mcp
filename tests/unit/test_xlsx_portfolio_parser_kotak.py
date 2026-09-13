"""Golden test against a real, live-fetched Kotak ELSS Tax Saver Fund sheet (August 2026),
extracted from Kotak's combined monthly workbook by the adapter's own fetch() (see
kotak.py's docstring for why: the workbook's merged "Name of Instrument" header puts real
per-row data two columns to the right of where the shared parser's header-position detection
expects it, so the adapter repairs the name column and returns a single-sheet workbook before
handing it to the unmodified shared parser)."""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "kotak_elss_aug2026.xlsx"


def _parse():
    return parse_portfolio_xlsx(FIXTURE.read_bytes())


def test_reconciliation_passes():
    r = _parse()
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.01


def test_holdings_extracted():
    r = _parse()
    assert len(r.holdings) > 30
    icici = next(h for h in r.holdings if "ICICI BANK" in h.instrument_name.upper())
    assert icici.isin == "INE090A01021"
    assert icici.asset_class == "equity"
    assert abs(icici.pct_nav - 0.0524) < 1e-6


def test_grand_total_stated_exactly_at_100_percent():
    r = _parse()
    # Unlike some AMCs (UTI/Tata) that never print an explicit grand-total %, Kotak's
    # "Grand Total" row states exactly 100 (percentage points) -> normalised to 1.0 fractional.
    assert r.grand_total_pct_nav == 1.0
    assert not any("self-summed" in w for w in r.warnings)
