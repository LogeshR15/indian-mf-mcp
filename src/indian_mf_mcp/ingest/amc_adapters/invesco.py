"""Invesco Mutual Fund (India) adapter.

Invesco was previously recorded as blocked: "Commercial WAF on the whole domain ...
Invesco's India business may also have been rebranded, possibly making it moot." Both halves
of that verdict were re-tested live 2026-09-14 and neither holds up.

**Entity status.** Invesco Asset Management (India) is not defunct and has not been renamed.
Its history: Religare Invesco Asset Management (2013) -> Invesco Asset Management after
Invesco bought out Religare's stake (2015-16) -> in April 2024 Hinduja Group's IndusInd
International Holdings Ltd acquired a 60% stake, becoming joint sponsor of Invesco Mutual Fund
alongside the US-based Invesco (all regulatory approvals completed by late 2025). No renaming
of the AMC or its funds has been announced; every scheme and document on the live site today
is still branded "Invesco India ...". So this is a real, live, AMFI-registered AMC filing
disclosures under its existing name -- not a defunct/rebranded entity.

**The WAF verdict does not hold either**, at least not for the current site. `https://www
.invescomutualfund.com/` returns a clean `200` to this project's honest User-Agent on the bare
homepage, on `/financials`, and on every path probed below -- no CloudFront/AWS-WAF challenge
observed anywhere. (Whatever produced the original CloudFront/WAF verdict was either a
different, older version of the site, or mis-attributed from probing WhiteOak Capital, its
sibling in that same blocked-list entry.) The one genuinely stale thing is AMFI's own
registered URLs: `https://invescomutualfund.com/literature-and-form?tab=Statutory` and
similar `?tab=...` links all 301-redirect to `/literature-forms/forms/application`, silently
*dropping* the `tab` query string -- the site was rebuilt (now Next.js App Router, React
Server Components) and the old query-param-driven route no longer exists. AMFI's registry is
simply pointing at a dead convention; the live site's own navigation uses path segments
instead (`/literature-forms/monthly-holdings`, `/literature-forms/fortnightly-holdings`, etc),
and "Monthly Holdings" is very much a first-class, separate category from "Fortnightly
Holdings" and "Half Yearly Holdings" (contradicting the concern that only fortnightly/
half-yearly might exist here) -- so a monthly cadence adapter is exactly what this AMC needs
and supports.

**Discovery.** The `/literature-forms/monthly-holdings/<classification>` pages are RSC-
streamed (`self.__next_f.push` chunks, same mechanism as 360 ONE) but that stream carries only
site navigation, not the document listing itself -- the listing is client-fetched from a
same-origin JSON API, found via a one-time build-time browser network capture (never used at
runtime; the adapter below is plain `httpx`):

    GET https://www.invescomutualfund.com/api/CompleteMonthlyHoldings
        ?year=<YYYY>&classification=<classification>

`classification` is mandatory (omitting it, or passing `year=0`, makes the endpoint fall back
to returning just the list of years with any data: `[{"Year":2026}, {"Year":2025}, ...,
{"Year":2012}]` -- confirmed the full list runs 2012-2026). Valid `classification` values are
scraped from the page's own tab bar, one per fund category:

    equity, fixed-income, hybrid, fund-of-funds, exchange-traded-fund, index-funds,
    fixed-maturity-plans

Each `(year, classification)` call returns **every scheme in that category for that year in
one response** -- not per-scheme, no pagination -- as a list of:

    {"Name": "Invesco India ELSS Tax Saver Fund",
     "JanUrl": "https://www.invescomutualfund.com/docs/default-source/.../elss...xlsx?sfvrsn=...",
     "FebUrl": "...", ..., "DecUrl": "...",
     "JanName": "01/26", ..., "DecName": "12/26"}

with an empty string for any month not yet published. The month-suffixed URLs are already
complete, absolute, `sfvrsn`-versioned document links on `www.invescomutualfund.com` itself
(the CMS's own "default-source" document-library convention -- no separate CDN/S3 host is
involved at all, unlike HDFC/Kotak) and are fetchable with a bare honest-UA GET, no auth, no
cookie. So discovery needs no scheme name, no URL guessing, and no probing: one GET per
(year, classification) hands back the exact same URLs the live site's own Download buttons
use. Total requests to cover the whole 2012-2026 archive: 15 years x 7 classifications = 105,
same order of magnitude as other AMCs' per-month probing loops.

Coverage totals for 2026 (ground-truthed 2026-09-14): 17 equity, 13 fixed-income, 5 hybrid,
6 fund-of-funds, 4 exchange-traded-fund, 2 index-funds, 0 fixed-maturity-plans (47 schemes).
`fixed-maturity-plans` is a real tab in the site's own UI but returns an empty array for every
year probed (2020-2026) -- Invesco currently runs no live FMP scheme; kept in the
classification list anyway so it starts returning data automatically if one ever launches,
at the cost of one harmless empty-array request per year.

**Format quirk, handled by the shared pipeline already, not by this adapter:** files from 2020
and earlier are legacy BIFF `.xls` (`tax-plan...xls`); the transition to real OOXML `.xlsx`
happens between 2020 and 2021 (verified: every 2020 URL sampled ends `.xls`, every 2021 one
ends `.xlsx`). This adapter does not filter by extension -- `portfolio_ingest.py`'s content
sniff already rejects non-XLSX as `skipped_format`, same as every other AMC with a legacy
tail. 2012-era filenames still carry the pre-2016 "religare_tax_plan_MMYY.xls" naming, a
visible fossil of the AMC's Religare Invesco days.

**Footer noise, harmless.** Invesco's footer text ("(3) Net Assets Value per unit...",
"SCHEME RISK-O-METER", "... Includes shares lent under Securities Lending & Borrowing Scheme
of BSE") isn't covered by `xlsx_portfolio.py`'s `FOOTER_STOP_MARKERS`, so a handful of
extra pseudo-holding rows with `isin=None` and `pct_nav=None` survive into
`PortfolioParseResult.holdings` alongside the real ones. They carry no percentage weight so
reconciliation is unaffected (verified exact 100.0% on the August 2026 ELSS Tax Saver fixture,
80 of 93 rows carrying a real ISIN); not fixed here per this project's "don't bend the shared
parser for one AMC's footer wording unless reconciliation actually breaks" rule -- it doesn't.

One file per scheme per month (PPFAS/Union-style, not a combined workbook); no `sheet_resolver`
needed. Percentage values in the fixture are already percentage-points (grand total = 100.0,
`xlsx_portfolio.py`'s existing scale auto-detection normalises to fractional automatically).
"""
from __future__ import annotations

import calendar
from datetime import date

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

API_URL = "https://www.invescomutualfund.com/api/CompleteMonthlyHoldings"

# Every "classification" tab on the live Monthly Holdings page (scraped from its own tab
# bar, not guessed). "fixed-maturity-plans" is real but empty in every year probed so far --
# see module docstring.
CLASSIFICATIONS = (
    "equity", "fixed-income", "hybrid", "fund-of-funds",
    "exchange-traded-fund", "index-funds", "fixed-maturity-plans",
)

# JSON field prefixes, in month order. Hardcoded rather than derived from calendar.month_abbr
# to stay independent of any locale the host process happens to have configured.
_MONTH_ABBRS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _matches_hint(name: str, hint_l: str) -> bool:
    name_l = name.strip().lower()
    return hint_l in name_l or name_l in hint_l


class InvescoAdapter:
    amc_id = "amc-invesco"

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []

        headers = {"User-Agent": config.USER_AGENT}
        hint_l = scheme_hint.strip().lower() if scheme_hint else None
        owns_client = client is None
        c = client or httpx.Client(timeout=30, follow_redirects=True)

        refs: list[DocumentRef] = []
        seen: set[tuple[str, date]] = set()
        try:
            for year in range(since.year, date.today().year + 1):
                for classification in CLASSIFICATIONS:
                    try:
                        resp = c.get(API_URL, headers=headers,
                                      params={"year": year, "classification": classification})
                        resp.raise_for_status()
                        entries = resp.json()
                    except (httpx.HTTPError, ValueError):
                        continue  # transient; a later run re-probes this (year, classification)
                    if not isinstance(entries, list):
                        continue

                    for entry in entries:
                        name = str(entry.get("Name") or "")
                        if not name:
                            continue
                        if hint_l is not None and not _matches_hint(name, hint_l):
                            continue

                        for month_idx, abbr in enumerate(_MONTH_ABBRS, start=1):
                            url = str(entry.get(f"{abbr}Url") or "").strip()
                            if not url:
                                continue
                            as_of = _month_end(year, month_idx)
                            if as_of < since or as_of > date.today():
                                continue
                            key = (url.split("?")[0], as_of)
                            if key in seen:
                                continue
                            seen.add(key)
                            refs.append(DocumentRef(
                                url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                as_of_date=as_of, scheme_hint=scheme_hint or name,
                            ))
        finally:
            if owns_client:
                c.close()

        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=90)
        resp.raise_for_status()
        return resp.content
