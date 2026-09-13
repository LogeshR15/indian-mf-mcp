# Indian Mutual Fund Intelligence MCP

Research-and-evidence MCP server for Indian mutual funds. See `spec.md` for the full
architecture proposal. Retrieves, normalizes and computes NAV, portfolio, and document
evidence with provenance — it does not score, rate, or recommend funds, and does not attempt
true performance attribution (not computable from Indian public disclosure).

Not investment advice.

## Status

- **Phase 1 (evidence spine):** AMFI NAVAll.txt daily ingest, historical NAV backfill,
  `resolve_fund`, and `get_fund_performance`.
  - NAVAll.txt is a *one-day snapshot*, so it alone can only ever hold one NAV point per plan
    and every return/risk metric stays uncomputable. `backfill-nav-history` fills the series
    from AMFI's `DownloadNAVHistoryReport_Po.aspx`, which orders its columns differently from
    NAVAll.txt — both are parsed by one column-header-driven parser rather than by field
    position, so AMFI reordering or inserting columns does not silently corrupt the read.
    All three scheme universes are fetched (`tp=1` open-ended, `2` close-ended, `3` interval).
    Requests are chunked by calendar month (a 3-month range is ~74 MB), each completed month
    is recorded in `ingest_run`, and NAV writes upsert on `(plan_id, date)` — so an
    interrupted multi-year backfill resumes where it stopped rather than re-downloading
    gigabytes, and a single failed month is recorded and skipped past instead of aborting the
    run. Unlike NAVAll.txt the raw payloads are not archived: a decade is several GB and,
    unlike the daily snapshot's point-in-time taxonomy, it stays re-derivable on demand.
    Scheme codes not already known from the daily ingest are counted and skipped, never
    turned into half-populated scheme rows.
- **Phase 2 (portfolio spine):** live AMC portfolio XLSX parsing with 100%-reconciliation
  gating, ISIN-keyed change engine (corporate-action flagging, price/flow drift detection),
  holding persistence, concentration, and `get_fund_portfolio`.
  - **AMC coverage today (16): PPFAS, SBI, UTI, Mirae Asset, Motilal Oswal, Tata, Nippon
    India, DSP, Franklin Templeton, Baroda BNP Paribas, Sundaram, Union, LIC, Taurus,
    Bank of India, and HDFC** (see
    `ingest/amc_adapters/registry.py`), each verified live end-to-end with its own golden
    fixture test. Adding an AMC means (1) a real, live-verified way to discover its monthly
    portfolio files — a static link, or a documented backing endpoint found via a one-time
    offline Playwright network capture (never a headless browser at runtime, per the spec) —
    and (2) a golden fixture test proving the parser's column/header/percentage-scale
    auto-detection handles that AMC's layout. These twelve AMCs exercise real layout
    diversity: PPFAS/Mirae/DSP/Union share one shape (one file per scheme); SBI, Motilal
    Oswal, Tata, Nippon India, Baroda BNP Paribas, and Sundaram each publish one combined
    workbook per month (multiple sheets, one per scheme, most with a differently-laid-out
    "Index" sheet — SBI/Motilal Oswal/Tata call the code column "fund/scheme/short code",
    Baroda BNP Paribas calls it "Short Name", Sundaram calls it "ACRONYM", Nippon has no
    header row at all — all handled by one shared, column-detecting resolver rather than a
    hardcoded mapping per AMC); Franklin Templeton has no Index sheet at all (the sheet name
    itself is the code, resolved by scanning each sheet's own title row); UTI puts the ISIN
    column non-adjacent to the rest entirely and never states a %-to-NAV grand total
    (self-summing fallback); Tata labels its true 100% total row "NET ASSETS" and suffixes
    sub-totals; Franklin puts the ISIN column *before* the name column (the reverse of every
    other AMC). Mirae, Motilal Oswal, DSP, Baroda BNP Paribas, and Union's holdings parsing
    needed **zero** new parser code, confirming the generalization holds as new AMCs are
    added rather than just accumulating special cases. Note: DSP and Baroda BNP Paribas's
    discovered endpoints only ever serve the *latest* month, not full history — a real
    capability limit, not a bug.
  - **LIC, Taurus and Bank of India** were added in one parallel batch and each needed **zero**
    shared-parser changes, which is now the expected outcome rather than a happy accident.
    All three publish nothing usable in their page HTML, and all three turned out to need no
    Playwright capture either — the request shape was readable straight from a static asset
    the site already serves. LIC hides its files behind a 4-step jQuery AJAX filter chain
    (category → scheme code → year → month → file) whose POST shapes are spelled out in the
    page's own inline `<script>`; the final POST's `fund_name` field is decorative and does
    not affect which file is returned. Bank of India's tabs are rendered by a `NoCategoryCall()`
    handler whose source lives in a plain public `AjaxCall.js`, naming a single
    `POST /AjaxService.asmx/GetDocuments` that returns all 346 documents back to 2012 in one
    shot (its long tail mixes legacy `.xls`/`.xlsb` and ad-hoc BOI AXA-era `.pdf` entries into
    the clean `.xlsx` series that runs from ~Feb 2021 — the adapter skips anything it cannot
    date rather than guessing). Taurus is plain server-rendered Drupal needing no JS at all,
    but its year/month dropdowns are taxonomy-term IDs that are irregular and non-formulaic
    (2026→567, 2025→558, … 2012→63), so the adapter re-parses the `<select>` options on every
    discovery call instead of hardcoding a map; its filenames also omit the "Taurus" prefix,
    so scheme-hint matching has to be bidirectional — the one-directional check `union.py`
    uses would silently return zero documents.
  - **Fifteen AMCs were attempted and are genuinely blocked, not just unstarted** — each
    investigated live with real effort, spanning distinct failure modes: **Kotak Mahindra** is
    bot-protected outright. (**HDFC** was listed here too and is now live — see below; the
    lesson it taught applies to this whole list.) **ICICI
    Prudential** (F5 BIG-IP WAF, TLS-fingerprint-based), **Invesco** and **WhiteOak Capital**
    (CloudFront/AWS-WAF on the whole domain — Invesco's India business may also have been
    rebranded, making this one possibly moot), and **Edelweiss** (Akamai edge WAF blocking
    its portfolio API specifically) are each blocked by a commercial WAF vendor. **Axis** and
    **PGIM India** are both cases where the real API is same-origin but only reachable after
    client-side JS execution the site fingerprints and blocks in Playwright specifically — a
    different failure mode from a WAF blocking the API itself. **HSBC** has no discoverable
    disclosure page at all. **Three AMCs — Bandhan, JM Financial, and Mahindra Manulife —
    share a distinct and notable failure mode**: their APIs return a real `200 OK` with a
    `{"data"/"payload": "<base64>"}` body that decodes to high-entropy ciphertext, not JSON —
    application-layer payload encryption, not access control. Replaying even a captured real
    request fails; decrypting it would mean reverse-engineering each site's client-side
    crypto scheme, a fundamentally different and more invasive problem than a WAF bypass, and
    explicitly out of scope. The repetition across three unrelated AMCs suggests a shared
    fintech backend vendor for investor-portal APIs — worth checking before investing more
    time on any AMC that returns this same opaque-payload shape. **Quant**'s self-service
    disclosure page appears to be simply broken (empty ASP.NET WebForms viewstate, zero
    backing requests fire) rather than protected. **Canara Robeco**'s file discovery actually
    *works* cleanly (static links, exact 100% reconciliation, zero parser changes) — but its
    WAF blocks the project's honest User-Agent while accepting a spoofed browser UA; asked
    the user explicitly rather than deciding unilaterally, and **the decision was to skip
    it** and keep the honest-User-Agent policy from spec.md intact. **Aditya Birla**'s
    discovery endpoint is already found and documented but its file lives behind a CDN host
    this *sandbox's* network policy blocks (not a real-world blocker).
  - **HDFC moved from "blocked outright" to live, and the reason generalizes.** Its listing
    host and its *file* host are different machines with different rules:
    `www.hdfcfund.com` returns a flat edge-level `403 Access Denied` to the honest
    User-Agent on every path including the bare homepage (not a CAPTCHA — a browser is never
    challenged), while `files.hdfcfund.com` is a public S3 bucket that serves that same honest
    User-Agent a clean `200` and the real workbook. Fetching was never blocked; only discovery
    was. And discovery turned out not to need the listing page at all, because the S3 key is
    fully computable:
    `/s3fs-public/<YYYY-MM>/Monthly <SCHEME> - <D Month YYYY>.xlsx`, where the folder is the
    month *after* the as-of month and the day is not zero-padded. Three keys guessed from that
    template — for months and schemes never observed — all returned real workbooks, and the
    August 2026 Flexi Cap file parses at exact 100% reconciliation with 95 holdings and zero
    parser changes. Quirks, all handled in the adapter: `HEAD` is denied bucket-wide (403 even
    for keys that exist), so existence is probed with `GET`+`Range: bytes=0-0` (206 = hit);
    an absent key returns **403, not 404**, because `s3:ListBucket` is denied so S3 reports
    `AccessDenied` rather than `NoSuchKey`; and keys are case-sensitive while HDFC's own casing
    is wildly inconsistent between schemes ("HDFC Nifty Metal ETF" vs "HDFC NIFTY SMALLCAP 250
    ETF"), so `scheme_hint` must be spelled as HDFC spells it. Verified present every month
    sampled back to March 2024, patchy before that — per-month probing skips gaps rather than
    guessing. **The general lesson: "blocked" was being decided by testing the AMC's investor
    portal, but the portal and the file host are often separate infrastructure with separate
    rules. The remaining entries on this blocked list were all judged on their portals, and
    none has yet been re-tested for an independently-reachable file host.**
  - The remaining ~21 AMCs haven't been attempted yet. This is real, per-AMC engineering
    effort — exactly what the spec calls "the real moat" of the project — but the pattern
    (Playwright discovery → adapter → golden test) is proven across twelve materially
    different AMC layouts, and the failure modes for the rest are now well-characterized
    (multiple WAF vendors, headless-JS fingerprinting, SPA-with-no-API, payload encryption, a
    broken page, or UA policy) rather than unknowns.

- **Phase 3 (document intelligence, partial):** PDF section extraction (SEBI-standard headings +
  keyword search) ground-truthed against a real AMFI-hosted SID, `get_document`, and
  `get_fund_profile` (identity, benchmark, verbatim mandate excerpts, realised Direct-vs-Regular
  cost spread computed from NAV alone). TER capture and fund-manager extraction from factsheets
  are not yet implemented and are reported as explicitly unavailable, never fabricated.

Remaining AMC adapters, TER/manager extraction, and change monitoring (Phase 4) are not yet built.

## Usage

```
uv sync
uv run mf-mcp ingest-navall   # populate the local store with AMFI's daily NAV universe
uv run mf-mcp backfill-nav-history --from 2016-01-01   # historical NAV; resumable, run once
uv run mf-mcp backfill-portfolio --amc ppfas \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "Parag Parikh Flexi Cap Fund" \
    --from 2019-01-01
uv run mf-mcp backfill-portfolio --amc sbi \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "SBI Flexicap Fund" \
    --from 2024-01-01
uv run mf-mcp backfill-portfolio --amc uti \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "UTI Flexi Cap Fund" \
    --from 2024-01-01
uv run mf-mcp backfill-portfolio --amc mirae \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "Mirae Asset Large Cap Fund" \
    --from 2024-01-01
uv run mf-mcp backfill-portfolio --amc motilal-oswal \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "Motilal Oswal Flexi Cap Fund" \
    --from 2024-01-01
uv run mf-mcp backfill-portfolio --amc tata \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "Tata Large & Mid Cap Fund" \
    --from 2024-01-01
uv run mf-mcp backfill-portfolio --amc nippon \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "Nippon India Large Cap Fund" \
    --from 2024-01-01
uv run mf-mcp backfill-portfolio --amc dsp \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "DSP Flexi Cap Fund" \
    --from 2024-01-01   # DSP's endpoint only ever serves the latest month, not full history
uv run mf-mcp backfill-portfolio --amc franklin-templeton \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "Franklin India Bluechip Fund" \
    --from 2024-01-01
uv run mf-mcp backfill-portfolio --amc baroda-bnp-paribas \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "Baroda BNP Paribas Large Cap Fund" \
    --from 2024-01-01   # only the latest month is available
uv run mf-mcp backfill-portfolio --amc sundaram \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "Sundaram Large and Mid Cap Fund" \
    --from 2013-01-01   # full archive available back to 2012-2013
uv run mf-mcp backfill-portfolio --amc union \
    --scheme-id <scheme_id from resolve_fund> \
    --scheme-hint "Union Flexi Cap Fund" \
    --from 2021-01-01
uv run mf-mcp serve           # run the MCP server (stdio)
```

Data lives in `~/.indian-mf-mcp/` (SQLite store + raw archive; never evicted).

Run tests with `uv run pytest -q`. One test (`test_ppfas_live_smoke.py`) hits the real PPFAS
site and is skipped automatically if the network is unavailable.
