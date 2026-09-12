"""DSP Mutual Fund adapter.

Ground-truthed live 2026-09-13. The obvious disclosure page
(https://www.dspim.com/mandatory-disclosures/portfolio-disclosures) is static HTML with real
xlsx links — but every link there is either a "fund-performance" returns/AUM summary or a
debt-only ISIN file, never the equity/ISIN-level holdings file. That file lives at a
per-scheme URL discovered by following a "Portfolio" link on an individual scheme's own
product page (e.g. https://www.dspim.com/invest/mutual-fund-schemes/equity-funds/flexi-cap-fund/dspeq-direct-growth):
`https://www.dspim.com/mandatory-disclosures/scheme-portfolio/<code>`, which 302-redirects
directly to the current month's `https://www.dspim.com/media/pages/docs/<code>.xlsx` — always
the *latest* disclosure, with no historical-month parameter discoverable this way (a real
capability limit of this endpoint, not a shortcut taken).

The `<code>` (e.g. "dspeq" for Flexi Cap Fund) is not derivable from the scheme name — it's
looked up from DSP's own sitemap.xml, which lists every scheme's product-page slug in the
form `/invest/mutual-fund-schemes/<category>/<fund-slug>/<code>-<plan>-<option>`. This mirrors
the project's other combined-workbook AMCs' philosophy: read the mapping from a source the
AMC itself publishes, never hard-code it.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import httpx

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef
from indian_mf_mcp.parsers.xlsx_portfolio import parse_portfolio_xlsx

SITEMAP_URL = "https://www.dspim.com/sitemap.xml"
PORTFOLIO_URL_TMPL = "https://www.dspim.com/mandatory-disclosures/scheme-portfolio/{code}"

_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
_SITEMAP_SCHEME_RE = re.compile(
    r"/invest/mutual-fund-schemes/([a-z0-9-]+)/([a-z0-9-]+)/"
    r"([a-z0-9]+)-(?:direct|regular|institutional)-(?:growth|idcw)$",
    re.IGNORECASE,
)


def _slug_words(slug: str) -> set[str]:
    return set(slug.replace("-", " ").lower().split())


class DSPAdapter:
    amc_id = "amc-dsp"

    def _find_code(self, scheme_hint: str, client: httpx.Client | None = None) -> str | None:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(SITEMAP_URL, headers=headers, timeout=30)
        resp.raise_for_status()

        target_words = _slug_words(scheme_hint.replace("DSP", "").replace("dsp", ""))
        best_code = None
        best_overlap = 0
        for loc in _LOC_RE.finditer(resp.text):
            m = _SITEMAP_SCHEME_RE.search(loc.group(1))
            if not m:
                continue
            _, fund_slug, code = m.groups()
            slug_words = _slug_words(fund_slug)
            overlap = len(target_words & slug_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_code = code
        return best_code if best_overlap > 0 else None

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None, client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO or not scheme_hint:
            return []
        code = self._find_code(scheme_hint, client=client)
        if code is None:
            return []

        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        url = PORTFOLIO_URL_TMPL.format(code=code)
        resp = get(url, headers=headers, follow_redirects=True, timeout=30)
        if resp.status_code != 200:
            return []

        # This endpoint only ever serves the current/latest disclosure — no way to request a
        # specific historical month. Peek the file's own "Portfolio as on <date>" text to
        # find its real as_of_date rather than guessing from today's date.
        result = parse_portfolio_xlsx(resp.content)
        as_of = None
        if result.as_of_date_str:
            for fmt in ("%B %d, %Y", "%B %d %Y"):
                try:
                    as_of = datetime.strptime(result.as_of_date_str, fmt).date()
                    break
                except ValueError:
                    continue
        if as_of is None or as_of < since:
            return []

        return [DocumentRef(url=str(resp.url), doc_type=DocType.MONTHLY_PORTFOLIO,
                             as_of_date=as_of, scheme_hint=scheme_hint)]

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
