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
  - **AMC coverage today: PPFAS, SBI, UTI, Mirae Asset, and Motilal Oswal** (see
    `ingest/amc_adapters/registry.py`), each verified live end-to-end with its own golden
    fixture test. Adding an AMC means (1) a real, live-verified way to discover its monthly
    portfolio files — a static link, or a documented backing endpoint found via a one-time
    offline Playwright network capture (never a headless browser at runtime, per the spec) —
    and (2) a golden fixture test proving the parser's column/header/percentage-scale
    auto-detection handles that AMC's layout. The five AMCs done so far already exercise real
    layout diversity: PPFAS/Mirae share one shape; SBI and Motilal Oswal each shift columns
    differently, use percentage-point scale, and publish one combined workbook per month
    (multiple sheets, one per scheme, with an "Index" sheet whose own column layout also
    differs between the two — handled by a shared, column-detecting resolver rather than a
    hardcoded mapping); UTI puts the ISIN column non-adjacent to the rest entirely and never
    states a %-to-NAV grand total (the parser falls back to self-summing holdings for
    reconciliation). The parser detects column positions, header wording, and percentage
    scale per-file rather than assuming any one AMC's layout — Mirae and Motilal Oswal's
    *holdings* parsing needed zero new parser code, only new discovery/adapter code.
  - The remaining ~40 AMCs are not yet wired up. Checked live so far: HDFC blocks bots
    outright; Aditya Birla's real files live behind a CDN host (azureedge.net) blocked by
    this *sandbox's* network policy specifically, not a real-world blocker — its discovery
    endpoint (`CustomApi/Resources/FactsheetAccordionById`) is already found and documented,
    just not yet fetchable/verifiable from here; Canara Robeco needs further investigation
    (page timed out on the last attempt). This is real, per-AMC engineering effort — exactly
    what the spec calls "the real moat" of the project — but the pattern (Playwright
    discovery → adapter → golden test) is now proven across five materially different AMC
    layouts and is repeatable.

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
uv run mf-mcp serve           # run the MCP server (stdio)
```

Data lives in `~/.indian-mf-mcp/` (SQLite store + raw archive; never evicted).

Run tests with `uv run pytest -q`. One test (`test_ppfas_live_smoke.py`) hits the real PPFAS
site and is skipped automatically if the network is unavailable.
