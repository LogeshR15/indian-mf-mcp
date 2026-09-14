"""Golden test against a real, live-fetched Invesco Mutual Fund file: Invesco India ELSS Tax
Saver Fund, August 2026, downloaded from `www.invescomutualfund.com/docs/default-source/...`
(the same-origin CMS document-library host named in invesco.py's module docstring -- no
separate CDN involved). No parser generalisation was needed: the file's header row and grand
total already match existing keyword/substring detection in the shared parser. A handful of
footer pseudo-rows (e.g. "SCHEME RISK-O-METER", NAV-per-unit disclosure text) survive as
holdings with isin=None and pct_nav=None -- harmless noise that carries no weight and does not
affect reconciliation, documented in invesco.py rather than patched into the shared parser.
"""
from pathlib import Path

from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

FIXTURE = Path(__file__).parent.parent / "fixtures" / "invesco_elss_tax_saver_aug2026.xlsx"


def test_reconciliation_passes_exact():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw)
    assert r.reconciliation_ok is True
    assert abs(r.grand_total_pct_nav - 1.0) < 0.005


def test_holdings_extracted_with_real_isin_and_weight():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw)
    assert len(r.holdings) > 30

    with_isin = [h for h in r.holdings if h.isin]
    assert len(with_isin) > 30

    hdfc_bank = next(h for h in r.holdings if h.instrument_name == "HDFC Bank Limited")
    assert hdfc_bank.isin == "INE040A01034"
    assert abs(hdfc_bank.pct_nav - 0.0431) < 0.001
    assert hdfc_bank.asset_class == "equity"

    icici_bank = next(h for h in r.holdings if "ICICI Bank" in h.instrument_name)
    assert icici_bank.isin == "INE090A01021"
    assert icici_bank.asset_class == "equity"


def test_as_of_date_parsed():
    raw = FIXTURE.read_bytes()
    r = parse_portfolio_xlsx(raw)
    assert r.as_of_date_str == "August 31, 2026"
