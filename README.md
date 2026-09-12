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
  - **AMC coverage today: PPFAS, SBI, UTI, Mirae Asset, Motilal Oswal, and Tata** (see
    `ingest/amc_adapters/registry.py`), each verified live end-to-end with its own golden
    fixture test. Adding an AMC means (1) a real, live-verified way to discover its monthly
    portfolio files — a static link, or a documented backing endpoint found via a one-time
    offline Playwright network capture (never a headless browser at runtime, per the spec) —
    and (2) a golden fixture test proving the parser's column/header/percentage-scale
    auto-detection handles that AMC's layout. These six AMCs exercise real layout diversity:
    PPFAS/Mirae share one shape; SBI, Motilal Oswal, and Tata each publish one combined
    workbook per month (multiple sheets, one per scheme, each with a differently-laid-out
    "Index" sheet — handled by a shared, column-detecting resolver rather than a hardcoded
    mapping) and shift column positions differently; UTI puts the ISIN column non-adjacent to
    the rest entirely and never states a %-to-NAV grand total (self-summing fallback); Tata
    labels its true 100% total row "NET ASSETS" (no "total" in it at all) and suffixes
    sub-totals ("PORTFOLIO TOTAL") where every other AMC prefixes them ("GRAND TOTAL") — both
    required generalizing the aggregate-row and grand-total detection in the parser itself.
    Mirae and Motilal Oswal's holdings parsing needed **zero** new parser code, confirming the
    generalization is holding as new AMCs are added, not just accumulating special cases.
  - **Five more AMCs were attempted and are genuinely blocked, not just unstarted** — each
    investigated live with real effort, not a quick check: **HDFC** and **Kotak Mahindra**
    are both bot-protected (HDFC: generic 403; Kotak: Radware Bot Manager CAPTCHA on every
    request, confirmed via both Playwright and httpx). **Axis** is a Next.js App Router SPA
    with portfolio links rendered via React Server Components and no static or XHR entry
    point found (same class of problem the spec already flagged for AMFI's own `www` site).
    **HSBC** has no discoverable disclosure page at all — the one candidate link resolves to
    a CAMS SPA needing session context that wasn't reconstructable. **Canara Robeco** is the
    one interesting case: the actual file discovery *worked* (static WordPress-media XLSX
    links, 66 holdings, exact 100% reconciliation, zero parser changes needed) — but its WAF
    403s the project's own honest User-Agent string specifically while accepting a spoofed
    browser UA. Per the spec's explicit policy ("honest User-Agent"), no adapter was built
    rather than silently shipping a spoofed identity — **this is a policy decision for a
    human, not something to decide unilaterally**: if you want Canara Robeco covered, say so
    and specify whether spoofing the UA is acceptable for this AMC. **Aditya Birla**'s
    discovery endpoint is already found and documented but its file lives behind a CDN host
    this *sandbox's* network policy blocks (not a real-world blocker).
  - The remaining ~35 AMCs haven't been attempted yet. This is real, per-AMC engineering
    effort — exactly what the spec calls "the real moat" of the project — but the pattern
    (Playwright discovery → adapter → golden test) is proven across six materially different
    AMC layouts, and the failure modes for the rest are now well-characterized (bot
    protection, SPA-with-no-API, or WAF/UA policy) rather than unknowns.

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
uv run mf-mcp serve           # run the MCP server (stdio)
```

Data lives in `~/.indian-mf-mcp/` (SQLite store + raw archive; never evicted).

Run tests with `uv run pytest -q`. One test (`test_ppfas_live_smoke.py`) hits the real PPFAS
site and is skipped automatically if the network is unavailable.
