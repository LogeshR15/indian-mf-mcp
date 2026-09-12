# Indian Mutual Fund Intelligence MCP

Research-and-evidence MCP server for Indian mutual funds. See `spec.md` for the full
architecture proposal. Retrieves, normalizes and computes NAV, portfolio, and document
evidence with provenance — it does not score, rate, or recommend funds, and does not attempt
true performance attribution (not computable from Indian public disclosure).

Not investment advice.

## Status

- **Phase 1 (evidence spine):** AMFI NAVAll.txt ingest + `resolve_fund` + `get_fund_performance`.
- **Phase 2 (portfolio spine):** live AMC portfolio XLSX parsing with 100%-reconciliation
  gating, ISIN-keyed change engine (corporate-action flagging, price/flow drift detection),
  holding persistence, concentration, and `get_fund_portfolio`.
  - **AMC coverage today (11): PPFAS, SBI, UTI, Mirae Asset, Motilal Oswal, Tata, Nippon
    India, DSP, Franklin Templeton, Baroda BNP Paribas, and Sundaram** (see
    `ingest/amc_adapters/registry.py`), each verified live end-to-end with its own golden
    fixture test. Adding an AMC means (1) a real, live-verified way to discover its monthly
    portfolio files — a static link, or a documented backing endpoint found via a one-time
    offline Playwright network capture (never a headless browser at runtime, per the spec) —
    and (2) a golden fixture test proving the parser's column/header/percentage-scale
    auto-detection handles that AMC's layout. These eleven AMCs exercise real layout
    diversity: PPFAS/Mirae/DSP share one shape (one file per scheme); SBI, Motilal Oswal,
    Tata, Nippon India, Baroda BNP Paribas, and Sundaram each publish one combined workbook
    per month (multiple sheets, one per scheme, most with a differently-laid-out "Index"
    sheet — SBI/Motilal Oswal/Tata call the code column "fund/scheme/short code", Baroda BNP
    Paribas calls it "Short Name", Sundaram calls it "ACRONYM", Nippon has no header row at
    all — all handled by one shared, column-detecting resolver rather than a hardcoded
    mapping per AMC); Franklin Templeton has no Index sheet at all (the sheet name itself is
    the code, resolved by scanning each sheet's own title row); UTI puts the ISIN column
    non-adjacent to the rest entirely and never states a %-to-NAV grand total (self-summing
    fallback); Tata labels its true 100% total row "NET ASSETS" and suffixes sub-totals;
    Franklin puts the ISIN column *before* the name column (the reverse of every other AMC).
    Mirae, Motilal Oswal, DSP, and Baroda BNP Paribas's holdings parsing needed **zero** new
    parser code, confirming the generalization holds as new AMCs are added rather than just
    accumulating special cases. Note: DSP and Baroda BNP Paribas's discovered endpoints only
    ever serve the *latest* month, not full history — a real capability limit, not a bug.
  - **Ten AMCs were attempted and are genuinely blocked, not just unstarted** — each
    investigated live with real effort, spanning distinct failure modes: **HDFC** and
    **Kotak Mahindra** (Radware Bot Manager CAPTCHA) are bot-protected outright. **ICICI
    Prudential** (F5 BIG-IP WAF, TLS-fingerprint-based), **Invesco** (CloudFront/AWS-WAF on
    the whole domain — its India business may also have been rebranded, making this possibly
    moot), and **Edelweiss** (Akamai edge WAF blocking its portfolio API specifically) are
    each blocked by a different commercial WAF vendor. **Axis** is a Next.js App Router SPA
    with portfolio links rendered via React Server Components and no static or XHR entry
    point found. **HSBC** has no discoverable disclosure page at all. **Bandhan** is a
    genuinely different failure mode: its API payload is end-to-end encrypted client-side
    (custom signature headers computed by obfuscated JS), not just access-controlled —
    replaying even a captured real request returns "Invalid encrypted data". **Quant**'s
    self-service disclosure page appears to be simply broken (empty ASP.NET WebForms
    viewstate, zero backing requests fire) rather than protected. **Canara Robeco**'s file
    discovery actually *works* cleanly (static links, exact 100% reconciliation, zero parser
    changes) — but its WAF blocks the project's honest User-Agent while accepting a spoofed
    browser UA; asked the user explicitly rather than deciding unilaterally, and **the
    decision was to skip it** and keep the honest-User-Agent policy from spec.md intact.
    **Aditya Birla**'s discovery endpoint is already found and documented but its file lives
    behind a CDN host this *sandbox's* network policy blocks (not a real-world blocker).
  - The remaining ~28 AMCs haven't been attempted yet. This is real, per-AMC engineering
    effort — exactly what the spec calls "the real moat" of the project — but the pattern
    (Playwright discovery → adapter → golden test) is proven across eleven materially
    different AMC layouts, and the failure modes for the rest are now well-characterized
    (multiple WAF vendors, SPA-with-no-API, payload encryption, a broken page, or UA policy)
    rather than unknowns.

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
uv run mf-mcp serve           # run the MCP server (stdio)
```

Data lives in `~/.indian-mf-mcp/` (SQLite store + raw archive; never evicted).

Run tests with `uv run pytest -q`. One test (`test_ppfas_live_smoke.py`) hits the real PPFAS
site and is skipped automatically if the network is unavailable.
