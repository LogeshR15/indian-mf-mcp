# Indian Mutual Fund MCP

An MCP server that gives Claude (or any MCP client) **evidence** about Indian mutual funds —
NAV history, portfolio holdings, and scheme documents — normalized, reconciled, and tagged
with where every number came from.

It deliberately does **not** score, rate, or recommend funds.

> **Not investment advice.** This is a research tool. It reports what public disclosures say
> and how confident it is in each fact. Nothing it emits is a recommendation.

---

## What makes this different

Most fund data sources hand you a number and expect you to trust it. This one is built around
the opposite instinct:

- **Every fact carries its provenance.** Each value is tagged `official`, `calculated`,
  `observed`, `approximation`, or `inferred`, so a model reasoning over it can weight a
  regulator-filed holding differently from a figure derived with an error term.
- **Portfolios must reconcile to 100% or they don't load.** A monthly disclosure whose weights
  don't sum is rejected, not silently ingested. Partial data is worse than no data.
- **Gaps are reported, never filled.** If a fund's TER isn't available, the answer is
  "unavailable" — not a plausible guess.
- **Raw source files are archived forever.** AMFI's daily NAV file is the only record of
  point-in-time scheme taxonomy; once a day passes uncaptured, that history is gone.

## What it can answer

| Tool | What it gives you |
|---|---|
| `resolve_fund` | Turns a messy fund name, ISIN, or scheme code into unambiguous scheme + plan identity |
| `get_fund_performance` | Trailing and rolling returns, volatility, Sharpe/Sortino, drawdowns, stress windows, benchmark-proxy and category comparators |
| `get_fund_portfolio` | Holdings, sector/asset-class/market-cap allocation, month-over-month changes (with corporate-action and ISIN-change flags), concentration, persistence/conviction streaks, portfolio overlap between funds |
| `get_fund_profile` | Identity, benchmark, verbatim mandate excerpts, TER, realised Direct-vs-Regular cost spread, current managers |
| `get_document` | Section extraction from SIDs and other scheme PDFs |
| `list_disclosure_events` | Detected changes over time — manager changes, TER changes, benchmark changes — each tagged by how it was detected |

Always call `resolve_fund` first — Indian scheme names are genuinely ambiguous, with renames,
near-identical names across AMCs, and 4–8 plan/option variants per scheme.

---

## Quickstart

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/LogeshR15/indian-mf-mcp.git
cd indian-mf-mcp
uv sync
```

One command bootstraps a usable store — scheme universe, AMFI cap lists, and NAV history:

```bash
uv run mf-mcp setup
```

That defaults to **3 years** of daily NAV (~8 min). Pass `--years 10` for the full decade
(~35 min, ~17M points), or `--skip-nav-history` to stop after the scheme universe and be
querying in seconds. Every step is resumable and idempotent: completed months are recorded,
so an interrupted run — or a later `--years 10` to widen the window — does only the work
that is actually missing.

Check what you have at any time:

```bash
uv run mf-mcp status
```

It prints row counts and coverage windows per data class, then a numbered list of exactly
which commands would fill the remaining gaps. It exits non-zero only when the store cannot
answer anything at all, so you can gate a provisioning script on it.

### Load portfolio holdings

NAV and scheme identity cover every scheme; holdings are per-AMC and opt-in. Load an AMC in
one command — no scheme IDs to look up:

```bash
uv run mf-mcp backfill --amc ppfas --from 2023-01-01
```

`uv run mf-mcp amcs` lists all 31 supported AMC keys and how many schemes you have loaded
for each. Add `--doc-types portfolio,factsheet,addendum` to pull manager/TER history and
official change notices in the same pass.

### Run the server

```bash
uv run mf-mcp serve
```

Data lives in `~/.indian-mf-mcp/` — a SQLite store plus a never-evicted raw archive.
Override the location with `INDIAN_MF_MCP_HOME`.

### Connect it to Claude Code

```bash
claude mcp add indian-mf -- uv run --directory /path/to/indian-mf-mcp mf-mcp serve
```

`mf-mcp setup` prints this line with your own path already filled in and shell-quoted.

### Working with scheme IDs

Every per-scheme command takes a `--scheme-id`. Get one from the shell:

```bash
uv run mf-mcp resolve "parag parikh flexi cap"
```

```
Parag Parikh Flexi Cap Fund
  scheme_id    scheme-d41895e60e6fd859
  amc          PPFAS Mutual Fund
  category     Equity Scheme / Flexi Cap Fund
  plans        4  Direct/Growth, Direct/IDCW, Regular/Growth, Regular/IDCW
  nav through  2026-09-15
  also loaded  nav only
```

`also loaded` is the same availability flag `resolve_fund` returns to a model, so you can
see at a glance which tools will have data for that scheme. Add `--json` for the raw
payload.

---

## AMC coverage

**31 of ~53 AMCs** have working portfolio adapters:

360 ONE · Axis · Bajaj Finserv · Bandhan · Bank of India · Baroda BNP Paribas · DSP · Franklin
Templeton · Groww · HDFC · HSBC · ICICI Prudential · Invesco · ITI · Kotak Mahindra · LIC · Mirae
Asset · Motilal Oswal · Navi · Nippon India · PPFAS · quant · Quantum · SBI · Sundaram · Tata ·
Taurus · Trust · Union · UTI · Zerodha

NAV, scheme identity and taxonomy come from AMFI and cover **all** schemes — coverage gaps
affect portfolio holdings only.

Adding an AMC is the most valuable contribution you can make, and the most self-contained.
See **[docs/amc-coverage.md](docs/amc-coverage.md)** for per-AMC discovery notes, what's still
blocked and why, and **[CONTRIBUTING.md](CONTRIBUTING.md)** for the walkthrough.

---

## Known limitations

In keeping with "gaps are reported, never filled," worth stating plainly here too:

- **Portfolio parsing covers XLSX and legacy `.xls` (binary BIFF)**, sniffed by magic bytes
  rather than trusted by extension. A handful of AMCs occasionally publish PDF-only portfolio
  disclosures instead of a spreadsheet; those are detected and skipped rather than
  mis-parsed — the spec only calls for spreadsheet-format portfolio parsing (PDF extraction
  is reserved for SIDs, factsheets, and addenda, which `get_document` already covers).
- **Market-cap allocation (large/mid/small) covers 30 Jun 2022 onward.** `mf-mcp setup`
  (or `mf-mcp update-caplist`) discovers every half-yearly stock-categorisation
  spreadsheet AMFI has published and loads all nine, so the point-in-time join has a real
  series to choose from — a 2023 portfolio is classified against the list that was in
  force in 2023, never a newer one. Portfolios predating Jun 2022 report market cap as
  unavailable rather than being classified against a list that did not yet exist. AMFI
  publishes the ranking, not the buckets; the large/mid/small split applies SEBI's rank
  rule (1–100 / 101–250 / 251+) to it.
- **Addendum ingestion (`mf-mcp ingest-addendum` / `backfill-addenda`) covers manager, TER,
  benchmark and category-change notices, but relies on regex extraction over PDF/HTML text**,
  so a differently-worded notice can be missed. `list_disclosure_events` combines these
  `official`-confidence, addendum-sourced events with `observed`-confidence ones reconstructed
  by diffing factsheets — both are labelled, never blended.

None of this is silently papered over: the affected tool responses report the gap rather
than guessing.

---

## How it fits together

```
AMFI NAVAll.txt ─────┐
AMFI NAV history ────┼──► ingest ──► SQLite store ──► analytics ──┐
AMC portfolio files ─┤                (+ raw archive)             ├──► MCP tools ──► Claude
AMC / AMFI PDFs ─────┘                                            │
                                      change engine ──────────────┘
```

```
src/indian_mf_mcp/
├── ingest/          fetching and loading (NAV, portfolios, factsheets, SIDs, addenda)
│   └── amc_adapters/   one module per AMC — the main contribution surface
├── parsers/         AMFI delimited files, portfolio XLSX/XLS, PDF sections, format sniffing
├── normalize/       scheme taxonomy, plan/option parsing
├── analytics/       returns, risk, drawdown, benchmark proxy, cost spread, overlap
├── change_engine/   portfolio diffing, corporate actions, concentration, persistence
├── provenance/      epistemic tags and the two-tier fact/source wrapper every tool uses
├── store/           SQLite schema and repositories (portfolio, manager/TER, raw blob store)
└── tools/           the six MCP tools
```

Operationally the CLI splits in two: `setup` / `status` / `resolve` / `amcs` are the
onboarding surface, and `ingest-*` / `backfill*` / `health` are the data-loading surface.
`ingest/amc_identity.py` is the single place that reconciles the three AMC naming schemes
in play — the `--amc` adapter key (`ppfas`), the id an adapter declares (`amc-ppfas`), and
the id the store derives from AMFI's own name (`amc-ppfas-mutual-fund`).

`spec.md` holds the full architecture rationale, including which facts are deliberately *not*
computable from Indian public disclosure and why.

---

## Running tests

```bash
uv run pytest tests -q
```

Unit tests are offline and run against golden fixtures — real AMC files committed to
`tests/fixtures/`. Integration tests hit live AMC sites and skip automatically when the
network is unavailable. To stay offline:

```bash
uv run pytest tests/unit -q
```

---

## Contributing

Contributions are welcome, especially new AMC adapters.
Start with **[CONTRIBUTING.md](CONTRIBUTING.md)** — it covers the adapter walkthrough, the
project's data-integrity rules, and the sourcing policy.

One rule is worth stating up front, because it shapes everything else:

> **We do not evade access controls.** No spoofed User-Agents, no proxy rotation, no TLS
> fingerprint spoofing, no CAPTCHA solving. If an AMC blocks this project's honest
> User-Agent, the answer is to find infrastructure that isn't blocked — or to record it as
> blocked and move on.

That rule has cost us at least one otherwise-clean integration. It stays.

---

## Licence

Not yet chosen — see [#licensing](CONTRIBUTING.md#licence). Until a licence is added, default
copyright applies and contributions cannot be formally accepted.

## Data sources

Public disclosures from [AMFI](https://www.amfiindia.com/) and individual AMC websites. This
project is not affiliated with or endorsed by AMFI, SEBI, or any AMC.
