# Indian Mutual Fund MCP

**Ask an AI assistant real questions about Indian mutual funds, and get answers built from
the funds' own regulatory filings — with a source attached to every number.**

This is a research tool. It downloads what AMFI and the fund houses actually publish —
daily NAVs, monthly portfolio disclosures, scheme documents — onto your own computer,
checks the numbers add up, and makes them available to Claude (or any AI assistant that
speaks [MCP](#what-is-mcp)).

It deliberately does **not** score, rate, rank, or recommend funds.

> ### ⚠️ Not investment advice
>
> This tool reports what public disclosures say, and how confident it is in each fact.
> Nothing it produces is a recommendation to buy, sell, or hold anything. It is not a
> substitute for a SEBI-registered investment adviser. Mutual fund investments are subject
> to market risks; read all scheme-related documents carefully.

---

## Contents

- [Who this is for](#who-this-is-for)
- [What you can ask it](#what-you-can-ask-it)
- [Before you start](#before-you-start)
- [Install it](#install-it)
- [How to read the answers](#how-to-read-the-answers)
- [What it will not do](#what-it-will-not-do)
- [What data it covers](#what-data-it-covers)
- [Keeping it current](#keeping-it-current)
- [When something goes wrong](#when-something-goes-wrong)
- [Glossary](#glossary)
- [Known limitations](#known-limitations)
- [For developers](#for-developers)

---

## Who this is for

| If you are… | What you get | What it costs you |
|---|---|---|
| **A mutual fund researcher or analyst** | Point-in-time holdings, month-over-month portfolio changes, rolling returns, overlap between funds, manager and TER history — all traceable to a source document | ~20 minutes of setup, then it's a normal research tool |
| **A finance professional (RIA, analyst, journalist)** | An auditable evidence trail. Every figure says whether it came from a filing or was computed, and from what | Same setup; read [How to read the answers](#how-to-read-the-answers) before you cite anything |
| **A DIY investor comfortable with a terminal** | Honest answers about funds you already own or are considering, without a website trying to sell you something | Setup, plus the patience to accept "unavailable" as an answer |
| **A developer** | A clean MCP server with six read-only tools over a local SQLite store, 31 AMC adapters, and 483 tests | Read [For developers](#for-developers) |

**If you have never used a terminal**, this tool is honestly not yet a good fit — there is
no installer or app. You need to paste about six commands into a terminal window. If
you're willing to do that, the instructions below assume no prior knowledge and explain
what each command does.

---

## What you can ask it

Once it's connected, you talk to your AI assistant in plain English. It picks the right
tools and reads the data. Real examples:

**Holdings and what changed**
> "What did Parag Parikh Flexi Cap hold at the end of last month, and what changed versus
> the month before?"

> "Has Quantum Value Fund been trimming its bank exposure over the last year?"

> "How much do HDFC Flexi Cap and ICICI Prudential Value Discovery actually overlap?"

**Returns and risk**
> "Show me 3-year rolling returns for Mirae Asset Large Cap against its category."

> "How far did this fund fall in the March 2020 crash, and how long did it take to recover?"

> "What are the Sharpe and Sortino ratios for these three funds over 5 years, using the same
> risk-free rate?"

**Costs, managers, mandate**
> "Who manages this fund, and when did they take over?"

> "What's the real cost gap between the Direct and Regular plans of this fund?"

> "What does the scheme document actually say about its investment strategy? Quote it."

**Changes over time**
> "Has this fund changed its benchmark or its manager in the last three years?"

A good first question is simply: **"What can you tell me about Parag Parikh Flexi Cap
Fund?"**

> **One habit worth having:** fund names in India are genuinely ambiguous — near-identical
> names across fund houses, renames, and 4–8 plan variants per scheme. The assistant
> resolves the name first and will tell you if your query matched more than one fund. If
> the answer looks like it's about the wrong fund, say which AMC you meant.

---

## Before you start

**You will need:**

| Requirement | Why | How to check |
|---|---|---|
| A Mac, Linux, or Windows machine | It runs locally; no account, no server, no API key | — |
| **Python 3.10 or newer** | The tool is written in Python | `python3 --version` |
| **[uv](https://docs.astral.sh/uv/)** | Installs dependencies and runs the tool | `uv --version` |
| **~2 GB free disk** | Stores NAV history and archived source files | — |
| **An MCP-capable AI client** | e.g. [Claude Code](https://claude.com/claude-code) or the Claude desktop app | — |
| **Internet access** | To download data from AMFI and fund house websites | — |

**What it does not need:** no API keys, no paid data subscription, no account with anyone,
no cloud service. Everything lives on your machine.

**Roughly how long:** 5 minutes of typing, plus 8–35 minutes of downloading that runs
unattended.

If you don't have `uv`, install it first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Install it

### Step 1 — Get the code

```bash
git clone https://github.com/LogeshR15/indian-mf-mcp.git
cd indian-mf-mcp
uv sync
```

`uv sync` downloads the libraries the tool depends on. It prints a lot; that's normal.

### Step 2 — Build your data store

```bash
uv run mf-mcp setup
```

This is the one command that gets you a working store. It does three things:

1. **Downloads the full scheme universe** from AMFI — every scheme, plan, ISIN and
   category in India (~3,400 schemes, ~14,000 plans). Takes seconds.
2. **Downloads AMFI's market-cap classification lists** — all nine half-yearly editions
   back to June 2022, so a 2023 portfolio is classified against the 2023 list rather than
   today's.
3. **Downloads daily NAV history** — **3 years by default (~8 minutes)**.

Useful variations:

```bash
uv run mf-mcp setup --years 10        # full decade of NAV (~35 min, ~17M data points)
uv run mf-mcp setup --skip-nav-history # stop after step 2 — ready in seconds
```

Everything is **resumable**. Press Ctrl-C any time; rerunning picks up where it stopped.
Running it again later with `--years 10` downloads only the years you don't already have.

### Step 3 — Add portfolio holdings for the fund houses you care about

NAV and scheme identity cover **every** fund in India automatically. Holdings are
different — each fund house publishes them in its own format, so you load the ones you
want:

```bash
uv run mf-mcp backfill --amc ppfas --from 2023-01-01
```

That loads every PPFAS scheme's monthly holdings since Jan 2023 in one command. To see
the 31 supported fund houses and what you've already loaded:

```bash
uv run mf-mcp amcs
```

To also pull manager history, TER history and official change notices in the same pass:

```bash
uv run mf-mcp backfill --amc ppfas --from 2023-01-01 --doc-types portfolio,factsheet,addendum
```

> **Start with one or two fund houses.** Each takes a few minutes. You can always add more
> later — nothing is re-downloaded.

### Step 4 — Check what you have

```bash
uv run mf-mcp status
```

This prints how much of each kind of data you hold, and then a numbered list of exactly
which commands would fill whatever's still missing. It's the first thing to run whenever
something seems wrong.

### Step 5 — Connect it to your AI assistant

For **Claude Code**:

```bash
claude mcp add indian-mf -- uv run --directory /path/to/indian-mf-mcp mf-mcp serve
```

Replace `/path/to/indian-mf-mcp` with wherever you cloned it. `mf-mcp setup` prints this
line at the end with your own path already filled in and correctly quoted — copy it from
there.

For other MCP clients, the server command is `uv run --directory <path> mf-mcp serve`,
speaking MCP over stdio.

That's it. Start a new conversation and ask it about a fund.

### Where your data lives

Everything goes in `~/.indian-mf-mcp/` — a SQLite database plus an archive of every raw
file it ever downloaded. To put it elsewhere, set `INDIAN_MF_MCP_HOME`. To start over,
delete that folder and rerun `mf-mcp setup`.

---

## How to read the answers

This is the part that matters most if you're going to rely on, cite, or publish anything
you get out of this tool.

**Every single fact it returns carries a tag saying what kind of fact it is.** Most data
sources hand you a number and expect you to trust it equally. This one doesn't:

| Tag | What it means | How much weight to give it |
|---|---|---|
| `official` | Taken directly from a regulatory filing or a fund house's own disclosure, unaltered | Highest. This is what the fund itself filed. |
| `calculated` | Arithmetic this tool performed on official data — a CAGR from NAVs, a concentration ratio from holdings | High, but the method matters. The formula and inputs are stated. |
| `observed` | Inferred by comparing two filings over time — e.g. a manager change spotted by diffing consecutive factsheets | Medium. The change is real, but the *effective date* may be off, since you only know it happened between two documents. |
| `approximation` | A deliberate stand-in with a stated error term — e.g. a benchmark tracked via a passive index fund's NAV rather than the licensed index itself | Use with care. Never present these as the real thing; the payload names the proxy. |
| `inferred` | Derived by parsing or pattern-matching, e.g. splitting a scheme name into plan and option | Lowest. Usually right, occasionally not. |

Alongside the facts, every response carries a **sources** block: the document each number
came from, its date, its SHA-256 hash, and the URL it was fetched from. If you need to
verify a figure, you can go straight to the original file.

**Three rules the tool enforces on itself:**

- **Portfolios must reconcile to 100% or they don't load.** If a monthly disclosure's
  weights don't add up, it's rejected rather than quietly ingested. Partial holdings data
  is worse than none.
- **Gaps are reported, never filled.** If a fund's TER isn't available, the answer is
  "unavailable" — never a plausible-sounding guess.
- **Raw files are archived permanently.** AMFI's daily NAV file is the only record of what
  a scheme's category was on a given day. Once a day passes uncaptured, that history is
  gone for good.

---

## What it will not do

Being explicit about this, because these are deliberate design decisions, not gaps:

- **It will not rate, score, or rank funds**, or tell you what to buy. There is no
  star rating and no "best fund" list, because those require judgements this tool has no
  basis to make.
- **It will not give you performance attribution.** Decomposing returns into
  allocation/selection effects requires daily holdings. Indian funds disclose holdings
  *monthly*. Any attribution computed from monthly snapshots is fiction, so it isn't
  offered.
- **It will not compute returns for IDCW (dividend) plans.** AMFI's NAV for IDCW plans is
  not adjusted for distributions, so a CAGR computed from it would understate the real
  return, sometimes badly. It returns an explicit error and tells you to use the Growth
  plan instead.
- **It will not present a benchmark proxy as the real index.** Index values are licensed
  data. Where a benchmark can be tracked via a passive fund's NAV, the result is clearly
  flagged as a proxy and names the fund used.
- **It will not guess.** Every "unavailable" is a real answer about the limits of Indian
  public disclosure.

On Sharpe and Sortino: these use a stated risk-free rate, **6.5% annual by default**.
Sharpe ratios are only comparable across funds when computed with the *same* rate — if
you're comparing funds across a period when rates moved, set it explicitly and say what
you used.

---

## What data it covers

| Data | Coverage | Source | Updates |
|---|---|---|---|
| Scheme identity, categories, ISINs | **All** Indian mutual fund schemes (~3,400) | AMFI | Daily |
| Daily NAV | **All** schemes and plans | AMFI | Daily |
| NAV history | Back to whatever window you backfilled | AMFI | One-off, extendable |
| Portfolio holdings | **31 of ~53 fund houses** you choose to load | Fund house disclosures | Monthly |
| Market-cap classification | June 2022 onward | AMFI half-yearly lists | Twice a year |
| Managers, TER, change events | Schemes whose factsheets you've loaded | Fund house factsheets & addenda | Monthly |
| Scheme documents (SIDs etc.) | Documents you've loaded | Fund house PDFs | As published |

**Fund houses with working portfolio adapters (31):**

360 ONE · Axis · Bajaj Finserv · Bandhan · Bank of India · Baroda BNP Paribas · DSP ·
Franklin Templeton · Groww · HDFC · HSBC · ICICI Prudential · Invesco · ITI · Kotak
Mahindra · LIC · Mirae Asset · Motilal Oswal · Navi · Nippon India · PPFAS · quant ·
Quantum · SBI · Sundaram · Tata · Taurus · Trust · Union · UTI · Zerodha

NAV, scheme identity and category data come from AMFI and cover **every** scheme — the
coverage gap affects portfolio holdings only. See
**[docs/amc-coverage.md](docs/amc-coverage.md)** for which fund houses are still missing
and why.

---

## Keeping it current

NAV data goes stale within days. To refresh:

```bash
uv run mf-mcp ingest-navall     # daily — takes seconds
```

```bash
uv run mf-mcp backfill --amc ppfas --from 2026-01-01   # monthly, after disclosures post
```

To automate the daily NAV update, add it to `cron` (macOS/Linux):

```bash
0 20 * * * cd /path/to/indian-mf-mcp && uv run mf-mcp ingest-navall
```

`mf-mcp status` warns you when NAV data is more than a few days old.

---

## When something goes wrong

**Start with `uv run mf-mcp status`** — it usually names the exact command you need.

| Symptom | Cause | Fix |
|---|---|---|
| The assistant says it can't find a fund that definitely exists | Store is empty or NAV not loaded | `uv run mf-mcp setup` |
| "No portfolio data for this scheme" | That fund house's holdings aren't loaded | `uv run mf-mcp backfill --amc <name> --from 2023-01-01` |
| Managers or TER show as unavailable | Factsheets not loaded for that scheme | Rerun backfill with `--doc-types factsheet` |
| Market-cap allocation unavailable | Cap lists missing, or portfolio predates June 2022 | `uv run mf-mcp update-caplist` |
| Returns refused for a fund | You asked for an IDCW plan | Ask for the Growth plan |
| A fund house download fails | Their website changed or is blocking | `uv run mf-mcp health --amc <name>` |

To find a fund's internal ID from the terminal:

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

`also loaded` tells you at a glance which kinds of questions will have data behind them
for that fund.

---

## Glossary

For readers newer to Indian mutual funds or to AI tooling:

| Term | Meaning |
|---|---|
| **AMC** | Asset Management Company — the fund house (HDFC, SBI, PPFAS…) |
| **AMFI** | Association of Mutual Funds in India — the industry body that publishes daily NAVs and scheme data for every fund |
| **NAV** | Net Asset Value — the per-unit price of a fund, published daily |
| **TER** | Total Expense Ratio — the annual percentage a fund charges you |
| **Direct vs Regular** | Direct plans have no distributor commission, so they cost less and return more. Same portfolio, different fee. |
| **Growth vs IDCW** | Growth reinvests gains; IDCW (Income Distribution cum Capital Withdrawal, formerly "dividend") pays them out |
| **ISIN** | The unique 12-character international identifier for a specific plan |
| **SID** | Scheme Information Document — the legal document describing a fund's mandate, strategy and risks |
| **Factsheet** | A fund house's monthly summary: managers, TER, portfolio highlights |
| **Addendum** | An official notice of a change — new manager, changed TER, changed benchmark |
| **Rolling returns** | Returns measured over every possible window of a given length, rather than one lucky start date |
| **Drawdown** | How far a fund fell from its peak, and how long it took to recover |
| **Portfolio overlap** | How much two funds hold the same stocks — high overlap means less diversification than you think |
| <a name="what-is-mcp"></a>**MCP** | Model Context Protocol — an open standard that lets an AI assistant use external tools. This project is an MCP "server"; your AI assistant is the "client" |

---

## Known limitations

Stated plainly, in keeping with "gaps are reported, never filled":

- **Portfolio parsing covers XLSX and legacy `.xls`**, detected by file content rather than
  extension. A few fund houses occasionally publish PDF-only portfolio disclosures; those
  are skipped rather than mis-parsed. (PDF extraction is used for SIDs, factsheets and
  addenda, which `get_document` covers.)
- **Market-cap allocation covers June 2022 onward.** All nine half-yearly AMFI lists are
  loaded, so classification is point-in-time correct — a 2023 portfolio uses the 2023 list,
  never a newer one. Portfolios older than June 2022 report market cap as unavailable
  rather than being classified against a list that didn't exist yet. AMFI publishes the
  ranking, not the buckets; the large/mid/small split applies SEBI's rank rule
  (1–100 / 101–250 / 251+).
- **Change-notice (addendum) extraction uses pattern matching over PDF text**, so an
  unusually-worded notice can be missed. `list_disclosure_events` labels every event by how
  it was detected — `official` (from an addendum) or `observed` (reconstructed by diffing
  factsheets). The two are never blended.

None of this is papered over: the affected responses say so rather than guessing.

---

## For developers

### The six MCP tools

| Tool | What it returns |
|---|---|
| `resolve_fund` | A messy name, ISIN, or scheme code → unambiguous scheme + plan identity, plus availability flags per data class |
| `get_fund_performance` | Trailing and rolling returns, volatility, Sharpe/Sortino, drawdowns, named stress windows, benchmark-proxy and live category comparators |
| `get_fund_portfolio` | Holdings, sector/asset-class/market-cap allocation, month-over-month changes (corporate actions flagged, not asserted as trades), concentration, persistence streaks, pairwise overlap |
| `get_fund_profile` | Identity, benchmark, verbatim mandate excerpts, TER, realised Direct-vs-Regular cost spread, current managers |
| `get_document` | Section- or keyword-scoped extraction from SIDs and other scheme PDFs, with page numbers and hashes |
| `list_disclosure_events` | Manager/TER/benchmark/category changes over time, each tagged by detection method |

All six are read-only, idempotent, and closed-world — they never make a network call, so
clients can parallelize and cache them freely. `resolve_fund` should always be called
first.

### Architecture

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
├── store/           SQLite schema, repositories, raw blob store, store inventory
└── tools/           the six MCP tools
```

The CLI splits in two: `setup` / `status` / `resolve` / `amcs` are the onboarding surface;
`ingest-*` / `backfill*` / `health` are the data-loading surface.
`ingest/amc_identity.py` is the single place reconciling the three AMC naming schemes in
play — the `--amc` adapter key (`ppfas`), the id an adapter declares (`amc-ppfas`), and the
id the store derives from AMFI's own name (`amc-ppfas-mutual-fund`).

`spec.md` holds the full architecture rationale, including which facts are deliberately
*not* computable from Indian public disclosure, and why.

### Tests

```bash
uv run pytest tests -q
```

483 tests. Unit tests are offline and run against golden fixtures — real AMC files
committed to `tests/fixtures/`. Integration tests hit live AMC sites and are excluded from
the default run. To stay strictly offline:

```bash
uv run pytest tests/unit -q
```

### Contributing

Contributions are welcome, especially new AMC adapters — the most valuable and most
self-contained contribution available. Start with
**[CONTRIBUTING.md](CONTRIBUTING.md)**.

One rule is worth stating up front, because it shapes everything else:

> **We do not evade access controls.** No spoofed User-Agents, no proxy rotation, no TLS
> fingerprint spoofing, no CAPTCHA solving. If an AMC blocks this project's honest
> User-Agent, the answer is to find infrastructure that isn't blocked — or to record it as
> blocked and move on.

That rule has cost us at least one otherwise-clean integration. It stays.

---

## Licence

Not yet chosen — see [#licensing](CONTRIBUTING.md#licence). Until a licence is added,
default copyright applies and contributions cannot be formally accepted.

## Data sources

Public disclosures from [AMFI](https://www.amfiindia.com/) and individual AMC websites.
This project is not affiliated with, endorsed by, or connected to AMFI, SEBI, or any asset
management company.
