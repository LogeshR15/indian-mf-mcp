# Indian Mutual Fund Intelligence MCP — Product & Architecture Proposal

*Prepared 12 September 2026. All source claims below were probed live against the actual
endpoints on that date; findings are marked **[verified]**, **[reported]** (from
documentation/search, not hit directly), or **[unknown]**.*

---

## 1. Executive assessment

**Verdict: yes, build it — but not the thing that is easiest to build.**

The idea is sound, and it is sound for a specific reason that is worth naming precisely.

Indian mutual fund data splits into two very different populations:

| | Commodity | Scarce |
|---|---|---|
| **What** | NAV, scheme list, AUM | Portfolio holdings history, scheme documents, manager history, cost history, disclosure events |
| **Machine-readable?** | Yes | Partly — Excel and PDF scattered across ~45 AMC websites |
| **Already solved?** | Yes, several times over | No |
| **Historical depth available?** | Full, from AMFI | 5–7 years typically, AMC-dependent, never guaranteed |
| **Effort to serve well** | Hours | Months |

Every existing "Indian MF MCP server" I could find lives entirely in the left column — they
are thin wrappers over `mfapi.in` or `mftool`, which are themselves thin wrappers over AMFI's
NAV text file. **[verified]** They give Claude a number. They do not give Claude evidence.

Your framework (sections A–H) is overwhelmingly a **right-column** framework. Of your eight
analytical pillars, exactly one (D, returns) is served by the commodity layer. The other seven
need portfolios, documents, provenance, and history. That gap is the product.

**Three things I want to challenge before we design anything.**

**(1) Pillar G (performance attribution) cannot be delivered honestly, and you should cut it
from the MVP.** Indian MF disclosure gives you month-end holdings snapshots. It does not give
you transaction prices, intra-month trades, or dated cash flows. Any "attribution" computed
from two month-end snapshots is a *contribution approximation* with an error term you cannot
bound — it silently assumes positions were held statically across the month and ignores every
trade in between. For a fund with 30% turnover this is materially wrong, and wrong in a way
that looks authoritative. I recommend the MCP compute holding-level contribution
*approximations*, label them as such in the payload itself, and **refuse to emit a
sector/selection attribution decomposition at all**. Section 9 details this.

**(2) Pillar B (investment process) is mostly not extractable — it is readable.** "Sell
discipline", "position-sizing methodology", "capacity constraints" are not fields in any
disclosure. They appear, when they appear, as prose in a SID or a fund manager commentary.
The correct MCP behaviour is to *retrieve the right pages of the right document* and let
Claude read them. Any attempt to extract these into structured fields will produce confident
nonsense. This is the single most important design boundary in the system.

**(3) Pillar C (people) is reconstructable only by diffing documents over time, and only
back as far as an AMC keeps its archive.** There is no fund-manager-history dataset. You get
it by parsing the manager name off ~60 monthly factsheets and detecting the change. That
works, it is genuinely differentiated, and it is Phase 3 work — not MVP.

**What makes this MCP defensible**, if built right:

1. A **normalised, ISIN-keyed, month-indexed portfolio history** across AMCs — this does not
   exist publicly in usable form.
2. **Provenance on every fact**, which is what actually lets Claude reason without
   hallucinating.
3. **A document retrieval layer that returns sections, not whole PDFs.**
4. **Peer/category statistics computed from the full universe**, because no free authoritative
   source publishes category averages.

**What would make it worthless:** rebuilding a NAV API, exposing 25 tools, or presenting
approximations as measurements.

---

## 2. Existing ecosystem — what exists, what is missing

### 2.1 What already works well (do not rebuild)

| Thing | Status | Verdict |
|---|---|---|
| `portal.amfiindia.com/spages/NAVAll.txt` | **[verified]** 200 OK, 1.5 MB, 18,061 lines, semicolon-delimited, all live schemes with ISIN + scheme code + NAV + date | **Use directly.** This is the authoritative daily snapshot. |
| `portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?tp=1&frmdt=..&todt=..` | **[verified]** 200 OK, returns daily NAV for a date range, all schemes | **Use directly.** Authoritative historical NAV. |
| Same endpoint with `&mf=<amc_id>` | **[verified]** Returns one AMC only — e.g. `mf=53` + Jan-2013 returned Axis Midcap Direct Growth daily NAVs | **Use for chunked backfill.** This is the key to a tractable ingestion plan. |
| `mfapi.in` | **[verified]** free, no auth, per-scheme NAV history JSON, rate-limited, source not stated, no SLA | **Fallback only.** It is a re-host of AMFI. Do not make it primary — it breaks your provenance story. |
| `mftool`, `casparser`, `captn3m0/historical-mf-data` | **[reported]** mature Python libraries | Read them for parsing edge cases; don't depend on them at runtime. |
| Existing MF MCP servers (`mftool-mcp`, `mfapi-mcp-server`, FinStack, cf-stock-mcp) | **[reported]** all NAV/quote wrappers | **No overlap with this project.** Confirms the gap. |

**Important nuance on AMFI's historical NAV endpoint:** it returns *every scheme* for the date
range unless filtered by AMC. A naive "10 years of daily NAV for the whole universe" fetch is
enormous. The `mf=` parameter plus month-sized date windows makes backfill a bounded
~45 AMCs × 120 months = ~5,400 requests, done once. That is entirely feasible overnight.

### 2.2 The critical structural discovery

I fetched `https://www.amfiindia.com/online-center/portfolio-disclosure`. **[verified]**

AMFI does **not** host portfolio files. That page is a **registry of ~400 outbound links to
each AMC's own disclosure pages**, already segmented by document type. A sample of what is
embedded in that single page:

```
https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio
https://www.hdfcfund.com/statutory-disclosure/portfolio/fortnightly-portfolio
https://amc.ppfas.com/downloads/portfolio-disclosure/
https://www.sbimf.com/en-us/portfolios
https://mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio
https://www.miraeassetmf.co.in/downloads/portfolio
https://www.icicipruamc.com/news-and-media/downloads?currentTabFilter=Historical
https://www.motilaloswalmf.com/downloads/scheme-portfolio-details
https://www.canararobeco.com/documents/statutory-disclosures/scheme-dashboard/scheme-monthly-portfolio/
https://www.dspim.com/mandatory-disclosures/portfolio-disclosures
https://www.utimf.com/downloads/consolidate-all-portfolio-disclosure
https://www.jioblackrockamc.com/statutory-disclosure/disclosures/monthly-portfolio-disclosure
... ~45 AMCs, plus factsheet / annual-report / risk-o-meter / financials URLs for each
```

**Three consequences that shape the whole architecture:**

1. **The portfolio layer is inherently a per-AMC adapter problem.** There is no central feed.
   You will write and maintain ~45 small adapters. This is the real cost of the project, and
   the real moat.
2. **AMFI hands you the adapter bootstrap for free.** This page is a machine-readable seed
   list, refreshed by AMFI. Scrape it once into a registry; use it to detect when an AMC moves
   its disclosure page.
3. **AMFI's own `www` site is a client-rendered SPA and is not statically scrapeable.**
   **[verified]** — `/research-information`, `/ter-of-mf-schemes` and `/otherdata/scheme-details`
   all return 200 but ship their content via React Server Component flight payloads with no
   extractable links or backing API URLs in the HTML. By contrast the legacy
   `portal.amfiindia.com/*.aspx` and `/spages/*` endpoints are plain static files and work
   perfectly. **Design rule: build on `portal.amfiindia.com`, treat `www.amfiindia.com` as
   browser-only.**

### 2.3 Ground truth on portfolio file format

I downloaded PPFAS Flexi Cap's August 2026 monthly portfolio
(`PPFCF_PPFAS_Monthly_Portfolio_Report_August_31_2026.xlsx`) and parsed it. **[verified]**

- Real XLSX (not HTML-masquerading), 378 rows, one sheet.
- Header row 3: `Name of the Instrument | ISIN | Industry / Rating | Quantity | Market/Fair Value (Rs. in Lakhs) | % to Net Assets | YTM~ | YTC^`
- Section markers: `Equity & Equity related`, `(a) Listed / awaiting listing on Stock Exchange`, etc.
- **ISIN is present on every holding** (`INE040A01034` = HDFC Bank). This is the single most
  important fact in this document: ISIN makes cross-month position matching reliable.
- Industry strings use the standard AMFI/SEBI industry vocabulary (`Banks`, `IT - Software`,
  `Diversified FMCG`, `Pharmaceuticals & Biotechnology`), which is **consistent across AMCs** —
  so sector aggregation is comparable cross-AMC without a mapping layer. This was not obvious
  and is a meaningful win.
- Footer carries the benchmark name (`AMFI Tier 1 Benchmark ... Nifty 500 TRI`) and riskometer.
- PPFAS's own archive spans **2019–2026** (12/12/24/60/62/83/84/63 files per year). The fund
  launched in 2013 — so ~6 years of its life are **not** recoverable from the AMC site.

Two things the file does **not** contain, which matter:
- **No market-cap classification.** Large/mid/small must be derived by joining ISIN against
  AMFI's half-yearly stock categorisation list **[reported]** (top 100 = large, next 150 = mid,
  rest = small). That list changes every six months, so *point-in-time* market-cap allocation
  requires keeping the historical versions of the list — otherwise you will retroactively
  reclassify a 2021 portfolio using 2026 cutoffs and produce a false "style drift" signal.
  This is a real trap.
- **No cost basis, no transactions.** Confirming §9.

### 2.4 What is missing from the ecosystem entirely

- Normalised cross-AMC portfolio history
- Category/peer averages (must be computed)
- Fund manager change history
- TER history as a time series
- Benchmark TRI history at a sane price (see §3, the weakest link)
- Any provenance layer at all

---

## 3. Data-source map

Legend — **Difficulty**: 🟢 trivial · 🟡 moderate · 🔴 hard/expensive · ⚫️ not reliably possible.

### 3.1 Pillar A — Identity & mandate

| Datum | Primary source | Authority | Format | Freq | History | Difficulty |
|---|---|---|---|---|---|---|
| Scheme name, code, ISIN, plan, option | AMFI `NAVAll.txt` | Authoritative | CSV(`;`) | Daily | Live schemes only | 🟢 |
| AMC name | AMFI `NAVAll.txt` (section headers) | Authoritative | CSV | Daily | — | 🟢 |
| SEBI category / sub-category | AMFI `NAVAll.txt` section headers (`Open Ended Schemes(Equity Scheme - Mid Cap Fund)`) | Authoritative | CSV | Daily | Current only | 🟢 |
| Investment objective | SID (AMFI `portal.amfiindia.com/spages/<id>.pdf` **[verified]** — real SIDs served at sequential ids; or AMC site) | Authoritative | PDF prose | On revision | Current SID only | 🟡 |
| Benchmark | Portfolio XLSX footer **[verified]**; factsheet; SID | Authoritative | PDF/XLSX | Monthly | Reconstructable by diffing | 🟡 |
| Investment universe / restrictions | SID | Authoritative | PDF prose | On revision | Poor | 🟡 |
| Inception date | SID / factsheet | Authoritative | PDF | Static | — | 🟡 |
| Direct/Regular, Growth/IDCW | AMFI `NAVAll.txt` (explicit columns) | Authoritative | CSV | Daily | — | 🟢 |
| Fund age / track record | Derived from NAV series start | Derived | — | — | Full | 🟢 |
| AUM | AMFI monthly report `portal.amfiindia.com/spages/am<mon><yy>repo.xls` **[verified]** (category-level) + factsheet (scheme-level) | Authoritative | XLS/PDF | Monthly | Full archive on AMFI | 🟡 |

> ⚠️ **Category history is a genuine gap.** `NAVAll.txt` gives *today's* category. SEBI's 2017
> categorisation circular reshuffled the entire industry, and schemes have been merged and
> recategorised since. Reconstructing "what category was this fund in, in 2019" requires
> archived `NAVAll.txt` files or addenda. **Start archiving `NAVAll.txt` daily from day one** —
> it costs 1.5 MB/day and it is the only way you will ever have this.

### 3.2 Pillar B — Investment process

| Datum | Source | Format | Difficulty |
|---|---|---|---|
| Investment philosophy / strategy | SID "Investment Strategy" section; AMC philosophy pages | PDF prose | 🟡 retrieve, ⚫️ structure |
| Security selection, valuation framework, sell discipline, position sizing | SID prose, fund manager commentary in factsheets | PDF prose | 🟡 retrieve, ⚫️ structure |
| Style orientation (value/growth/quality/momentum) | **Not disclosed.** Inferable only from holdings + external fundamentals | — | ⚫️ as a fact; 🔴 as an inference |
| Top-down vs bottom-up | SID prose, if stated at all | PDF | ⚫️ |
| Typical holding period | Derivable from portfolio-history turnover | Derived | 🟡 |
| Capacity constraints | Occasionally in SID; usually only visible via subscription-halt addenda | PDF | ⚫️ |
| Portfolio ↔ philosophy consistency | **Claude's job**, given portfolio + SID text | — | n/a |

**This entire pillar is a document-retrieval problem, not a data problem.** Design accordingly.

### 3.3 Pillar C — People

| Datum | Source | Format | History | Difficulty |
|---|---|---|---|---|
| Current fund manager(s) | Monthly factsheet; SID | PDF | Current | 🟡 |
| Manager tenure / "managing since" | Factsheet (usually stated explicitly) | PDF | Current | 🟡 |
| Manager change events | **Addenda/notices** (AMCs must notify) + diffing archived factsheets | PDF | As deep as archive | 🔴 |
| Prior track record of a manager | Cross-reference manager name across all funds — only computable if you index every factsheet | Derived | — | 🔴 |
| Team structure, analyst headcount | Rarely disclosed; AMC "our team" pages | HTML | — | ⚫️ |
| Performance before/after manager change | NAV series split at the detected change date | Derived | Full | 🟢 *once* the date is known |

**Honest framing:** you can deliver manager *identity* cheaply, manager *change dates* with
real work, and manager *prior record* only after you've indexed the whole factsheet corpus.
Everything about team quality and key-person risk is Claude reading prose.

### 3.4 Pillar D — Returns

| Datum | Source | Difficulty |
|---|---|---|
| NAV history (full) | AMFI `DownloadNAVHistoryReport_Po.aspx` **[verified]** | 🟢 |
| Point-to-point & CAGR (1/3/5/7/10Y/SI) | Computed from NAV | 🟢 |
| Rolling returns, any window | Computed | 🟢 |
| vs benchmark | **Requires benchmark TRI series — see below** | 🔴 |
| vs category | Computed from full-universe NAV ingest | 🟡 (cheap once universe is ingested) |
| % of periods outperforming | Computed | 🟢 once benchmark exists |

> 🔴 **The benchmark TRI series is the weakest link in the entire design, and you should know
> it now.** Indian index TRI history is published by NSE Indices / BSE, but not via a free,
> stable, bulk-friendly, licence-clean endpoint. **[unknown — I did not verify NSE's current
> access terms, and you should before committing.]** Three options, none clean:
>
> 1. **Licence index data.** Correct, costs money, kills `uvx`-simplicity.
> 2. **Proxy the benchmark with a passive fund's NAV** — e.g. use a low-TER Nifty 500 index
>    fund's Direct-Growth NAV from AMFI as a Nifty 500 TRI proxy. This is *free, authoritative
>    in its own right, and already in your pipeline.* It is biased downward by the index fund's
>    TER and tracking error (~20–40 bps/yr). **Recommended for MVP, provided the payload
>    states the proxy explicitly and names the proxy fund.**
> 3. **Use category median as the comparator instead of the benchmark.** Free, fully
>    computable, arguably more useful for fund selection anyway. **Also recommended.**
>
> Do options 2 + 3 in MVP; treat licensed index data as a later upgrade. Never silently
> substitute a proxy — put `"comparator_type": "proxy"` in the payload.

### 3.5 Pillar E — Risk

Everything here is **computed from the NAV series**: volatility, beta, max drawdown, drawdown
duration and recovery, up/down capture, Sharpe, Sortino, information ratio, alpha, stress-window
returns, worst rolling periods. 🟢 — *except* that beta, capture, IR and alpha all need the
benchmark, inheriting §3.4's caveat. The risk-free rate for Sharpe/Sortino should come from RBI
T-bill data **[unknown]** or be a stated, overridable constant — and the MCP must report which
it used.

### 3.6 Pillar F — Portfolio

| Datum | Source | Format | Freq | History | Difficulty |
|---|---|---|---|---|---|
| Full holdings (name, ISIN, industry, qty, value, %NAV) | AMC monthly portfolio XLSX **[verified]** | XLSX/XLS/PDF | Monthly (debt: fortnightly) | 5–7y typical, AMC-dependent | 🟡 per-AMC, 🔴 across 45 |
| Sector allocation | Aggregated from the `Industry` column | Derived | Monthly | = holdings | 🟢 |
| Market-cap allocation | ISIN ⨝ AMFI half-yearly cap list **[reported]** | Derived | Monthly | Needs archived cap lists | 🟡 |
| Cash & equivalents | Portfolio file (TREPS / net receivables rows) | XLSX | Monthly | = holdings | 🟢 |
| Concentration (top-5/10/20, HHI) | Derived | — | Monthly | = holdings | 🟢 |
| Derivatives exposure | Portfolio file, separate section | XLSX | Monthly | = holdings | 🟡 sign/notional conventions vary |
| Foreign equity | Portfolio file, separate section | XLSX | Monthly | = holdings | 🟡 (no ISIN-to-cap-list match) |
| Portfolio turnover ratio | **Disclosed in factsheet** (AMC's own calc) | PDF | Monthly | = archive | 🟡 |
| Liquidity of holdings | ⚫️ Needs traded-volume data (exchange) | — | — | ⚫️ |

### 3.7 Pillar G — Attribution

See §9. Short version: holding-level contribution **approximation** 🟡; anything else ⚫️.

### 3.8 Pillar H — Cost

| Datum | Source | Difficulty |
|---|---|---|
| Current TER (Direct & Regular) | AMFI `ter-of-mf-schemes` page — **[verified]** exists but is SPA-rendered with no static link or discoverable API in the HTML. Falls back to each AMC's mandatory daily TER spreadsheet **[reported]**. | 🟡–🔴 |
| TER history | SEBI mandates *date-wise* disclosure **[reported]** → historical series should be obtainable, but only if you archive it yourself or find AMFI's backing API | 🔴 |
| Direct vs Regular gap | Trivially derived once both TERs are known | 🟢 |
| Exit load | SID / factsheet | 🟡 |
| Portfolio turnover | Factsheet | 🟡 |
| Transaction costs / brokerage | Scheme annual report (aggregate only) | 🔴 |
| Tax treatment | Regulatory knowledge, not per-fund data — **Claude already knows this; do not model it** | n/a |

> **Practical shortcut worth taking:** the Direct-vs-Regular TER gap can be *measured* from
> NAV alone, with high precision, by regressing the daily log-return difference between the
> Direct and Regular plans of the same scheme. Both series are free from AMFI. This gives you
> a realised, historical, fully-sourced cost differential without touching the TER page at all,
> and it is more analytically honest than a stated TER. **Recommend implementing this.**

### 3.9 Sources deliberately excluded, with reasons

| Source | Why not |
|---|---|
| Moneycontrol / Value Research / Morningstar | Aggregators. Destroys the provenance premise; ToS risk. |
| NSDL / CDSL | Depository data; not MF-scheme relevant. |
| Rating agencies | Credit ratings already appear in the portfolio file's `Industry / Rating` column for debt. |
| RBI | Only for the risk-free rate. One number. Include as a small, optional fetch. |
| Company filings / exchange data | Needed only for style attribution and liquidity — both out of MVP scope. A `screener`-style equity MCP can cover this *alongside* yours; don't absorb it. |
| SEBI | **Include**, but narrowly — see below. |

**On SEBI:** SEBI is authoritative for *rules*, not for *fund data*. Claude already knows SEBI
scheme categorisation. The only genuinely useful SEBI ingestion is the **categorisation
definitions** (to validate your taxonomy) and possibly **addenda-triggering circulars**. Do not
build a SEBI circular search engine. That is a different product.

---

## 4. First-principles data model

Nine entities. ISIN and `scheme_code` are the join keys that make everything else work.

```
AMC ──< Scheme ──< Plan ──< NavPoint
        │  │
        │  └──< Document ──< DocumentSection
        │  │
        │  └──< PortfolioSnapshot ──< Holding
        │  │
        │  └──< ManagerAssignment >── Manager
        │  │
        │  └──< ChangeEvent
        │
        └──< Benchmark (or proxy) ──< IndexPoint
```

```python
AMC            : amc_id, name, amfi_mf_code, disclosure_urls{portfolio,factsheet,sid,annual,addenda}, adapter_id
Scheme         : scheme_id, amc_id, name, category, sub_category, scheme_type(open/close/interval),
                 benchmark_id, inception_date, objective_text_ref, active, first_seen, last_seen
Plan           : plan_id, scheme_id, amfi_scheme_code, isin, plan(Direct|Regular), option(Growth|IDCW),
                 idcw_variant(Payout|Reinvest)          # <- the actual unit of NAV
NavPoint       : plan_id, date, nav                     # authoritative, AMFI
Manager        : manager_id, name_normalised, aliases[]
ManagerAssignment : scheme_id, manager_id, from_date, to_date|null, evidence_doc_id, confidence
PortfolioSnapshot : snapshot_id, scheme_id, as_of_date, disclosure_type(monthly|fortnightly|halfyearly),
                 source_doc_id, total_aum, retrieved_at, parse_confidence
Holding        : snapshot_id, isin|null, instrument_name, raw_name, industry_or_rating,
                 quantity, market_value_lakhs, pct_nav, asset_class(equity|debt|derivative|cash|foreign|reit),
                 listed(bool), section_label
Benchmark      : benchmark_id, name, is_proxy, proxy_plan_id|null, notes
Document       : doc_id, scheme_id|amc_id, doc_type(SID|KIM|FACTSHEET|PORTFOLIO|ANNUAL|ADDENDUM|SSD),
                 doc_date, url, sha256, content_type, retrieved_at, page_count, parse_status
ChangeEvent    : event_id, scheme_id, event_type, effective_date, detected_from(doc_id|diff),
                 before, after, confidence
Provenance     : (embedded on every emitted fact — see §6)
```

**Three modelling decisions that matter:**

1. **`Plan` is the atom, not `Scheme`.** AMFI's `NAVAll.txt` is plan-level; a single "fund" has
   4–8 NAV series. Every existing tool blurs this and produces wrong comparisons
   (Regular-vs-Direct is a ~100 bps/yr artefact). Default all analysis to **Direct/Growth**
   and say so.
2. **`Holding.isin` is nullable.** Cash, TREPS, derivatives and some unlisted instruments have
   no ISIN. The change engine must handle this (§9).
3. **`ChangeEvent` carries `confidence`.** A manager change detected from an addendum is a
   fact. One inferred by diffing two factsheets is an observation. Different epistemic status;
   model it, don't flatten it.

### 4.1 Taxonomy

Do **not** hard-code SEBI's category list. Derive it from `NAVAll.txt`'s own section headers —
they are literally `Open Ended Schemes(Equity Scheme - Mid Cap Fund)` **[verified]**, i.e. AMFI
publishes the canonical taxonomy inside the NAV file every single day, and it updates itself
when SEBI changes the rules. Parse it into `(scheme_type, category, sub_category)` and keep a
dated history of the string set. Maintain a small hand-written normalisation map for
`(Equity|Debt|Hybrid|Solution Oriented|Other)` roll-ups only. Passive/active and
domestic/international are inferred from sub-category plus name heuristics, and both should be
marked as `derived`, not `official`.

---

## 5. The minimum MCP tool set

**Recommendation: five tools.** Not one, not fifteen.

### 5.1 Why not one mega-tool

Your `get_fund_research_data(fund, areas=[...], period=...)` idea is tempting and I think it is
*almost* right — but it fails on three practical grounds:

- **Latency asymmetry.** `identity` is a cache hit in milliseconds. `portfolio` history for
  10 years may mean fetching and parsing 120 XLSX files on a cold cache. Fusing them means
  every call pays the worst case, and Claude cannot make a cheap follow-up query.
- **Payload size.** All seven areas at 10Y for one fund is well past any sane context budget.
  You would immediately need an `areas` filter — at which point you have re-invented separate
  tools with worse ergonomics and a less legible schema.
- **Error granularity.** If the AMC site is down, one tool returns a partial blob with an
  embedded error. Five tools let Claude see exactly what failed and route around it.

**However — adopt the good half of your idea:** every tool takes a `sections` (or equivalent)
parameter so Claude can ask for less, and **every fund-accepting parameter takes a list**, so
comparison is a parameter, not a tool. That kills the need for `compare_funds` entirely.

### 5.2 The five tools

---

#### `resolve_fund`

**Purpose.** Turn "PPFAS Flexi Cap" / "parag parikh" / an ISIN / a scheme code into an
unambiguous scheme + plan identity. Disambiguation front door.

**In.** `query: str | list[str]`, `limit=10`
**Out.** Candidates with `scheme_id`, canonical name, AMC, category/sub-category, all plan
variants with `amfi_scheme_code` + ISIN, inception date, active flag, and a `confidence` score.

**Source.** Local index built from `NAVAll.txt`. **[verified]**

**Why a tool.** Indian scheme names are a minefield — "Nippon India" vs "Reliance" (renamed),
"Bandhan" vs "IDFC" (renamed), 8 plan/option permutations per scheme, near-identical names
across AMCs. Claude guessing a scheme code silently produces analysis of the wrong fund. This
tool is cheap insurance and should be the documented first call.

**Why not Claude.** Claude cannot know today's scheme code list.

---

#### `get_fund_profile`

**Purpose.** Everything static or slow-moving about the fund. The "what am I buying" payload.

**In.** `scheme_ids: list[str]`, `as_of: date = today`,
`sections: list = ["identity","mandate","benchmark","costs","managers","documents"]`
**Out.**
```
identity   : name, AMC, category, sub-category, scheme type, inception, age, plans[]
mandate    : objective (verbatim excerpt + doc ref), stated strategy excerpt, universe,
             restrictions — ALL as text with page anchors, never as parsed fields
benchmark  : name, benchmark_id, is_proxy, proxy details
costs      : TER direct/regular (+ date), realised direct-regular NAV spread, exit load,
             latest disclosed turnover ratio
managers   : current manager(s), managing-since, evidence doc
documents  : index of available docs {type, date, url, doc_id, pages} — NOT contents
```
**Sources.** AMFI NAVAll (identity/taxonomy) · SID PDF (mandate) · factsheet (manager, TER,
benchmark, load) · portfolio XLSX footer (benchmark cross-check) · NAV series (realised spread).

**Why a tool.** Fuses 4–5 heterogeneous sources with provenance. Claude cannot fetch and parse
a SID PDF itself.

**Why the mandate stays as text.** Deliberate. See §1(2).

---

#### `get_fund_performance`

**Purpose.** The complete return/risk evidence pack, computed once with one methodology.

**In.** `scheme_ids: list[str]`, `plan="direct_growth"`, `period="10Y"|"since_inception"`,
`comparators=["benchmark","category"]`,
`metrics=["trailing","rolling","risk","drawdown","stress"]`,
`rolling_windows=[1,3,5]`, `nav_series=false`
**Out.** Trailing CAGR at standard horizons · rolling-return distributions (min/p5/p25/median/
p75/p95/max, % of windows beating comparator, % negative) · volatility, beta, Sharpe, Sortino,
information ratio, alpha, up/down capture · every drawdown > 10% with peak/trough/recovery
dates and durations · returns through named stress windows (COVID crash, 2022 drawdown, etc.)
· category rank and percentile. Raw NAV series only if explicitly requested.

**Sources.** AMFI historical NAV **[verified]** · benchmark proxy or licensed index ·
full-universe NAV for category stats.

**Why a tool.** Three independent reasons, and this is the clearest case in the whole design:
(a) the underlying data is ~2,500 daily points per fund — feeding that to Claude to compute
rolling returns is a catastrophic use of context and Claude will make arithmetic errors;
(b) rolling-window statistics over 10 years are genuinely expensive; (c) **methodology must be
identical across funds or comparisons are meaningless** — same calendar alignment, same
annualisation, same risk-free rate, same holiday handling.

**Why not Claude.** Claude doing this by hand is slow, wrong, and inconsistent between calls.

---

#### `get_fund_portfolio`

**Purpose.** Holdings, allocations, and change detection. The heart of the differentiated value.

**In.** `scheme_ids: list[str]`, `as_of: date|"latest"`, `compare_to: date|"prev_month"|null`,
`history: "none"|"12M"|"36M"|"60M"`,
`sections=["holdings","allocations","changes","concentration","persistence"]`,
`holdings_limit=null`
**Out.**
```
holdings      : full holdings @ as_of (name, ISIN, industry, qty, value, %NAV, asset class)
allocations   : by sector, asset class, market cap (with cap-list version stamped), cash
concentration : top-5/10/20 %, effective N (HHI), #holdings
changes       : vs compare_to — NEW / EXITED / INCREASED / REDUCED / UNCHANGED per position,
                each with qty delta, %NAV delta, and a corporate-action flag
persistence   : over `history` — for each ISIN, months held, continuous streak, avg %NAV,
                first/last seen. THIS is what answers "5-year conviction holdings".
history       : compact time series of allocations + concentration (not full holdings)
```
**Sources.** Per-AMC monthly portfolio files **[verified]** · AMFI cap list for market-cap join.

**Why a tool.** Requires per-AMC fetching, XLSX/PDF parsing, section-aware normalisation,
ISIN-keyed set arithmetic, and corporate-action handling. None of that is Claude's job. A 10-year
history is ~120 files and ~30,000 holding rows — far beyond a context window, and the compression
into persistence statistics is exactly what makes it usable.

**Why not Claude.** Claude *should* judge whether the change pattern indicates drift. It should
not be computing set differences over 30,000 rows.

---

#### `get_document`

**Purpose.** Retrieve source evidence — and specifically, *the relevant part* of it.

**In.** `doc_id` (from `get_fund_profile.documents`) **or** `scheme_id` + `doc_type` +
`as_of`; plus `query: str|null`, `sections: list|null`, `max_chars=15000`, `return="text"|"url"`
**Out.** Extracted text of the matching sections with page numbers, document metadata, sha256,
and the source URL. Never the whole PDF unless it is small and explicitly requested.

**Sources.** AMFI `portal.amfiindia.com/spages/<id>.pdf` for SIDs **[verified]** · AMC
factsheets, addenda, annual reports **[verified via the AMFI link registry]**.

**Why a tool.** PDFs are not fetchable or parseable by Claude directly, and a 120-page SID
is 200k+ tokens. Query-scoped section extraction is the difference between usable and not.

**Why not Claude.** Claude *does* read and interpret the returned prose. That is the whole point.

---

### 5.3 Capabilities that must NOT become tools

| Tempting tool | Why not |
|---|---|
| `compare_funds()` | It's `scheme_ids: list[...]` on the four data tools plus Claude's reasoning. A comparison tool would either duplicate the others or bake in an opinion about what "comparison" means. |
| `analyze_fund()` / `score_fund()` / `rate_fund()` | This is the entire thesis of the project inverted. The MCP would be smuggling in an unexaminable opinion. Refuse. |
| `check_style_drift()` | Drift is a judgement over evidence the other tools already return. Claude does it better and shows its reasoning. |
| `get_nav()` | Subsumed by `get_fund_performance`. A bare NAV lookup is the commodity layer. |
| `get_risk_metrics()`, `get_rolling_returns()`, `get_drawdowns()` | All `metrics=[...]` on one tool. Splitting them multiplies round-trips for one analysis. |
| `get_sector_allocation()`, `get_top_holdings()`, `get_portfolio_changes()` | All `sections=[...]` on `get_fund_portfolio`. |
| `get_manager_history()` | Belongs in `get_fund_profile.managers` once Phase 3 builds it. Not its own surface. |
| `search_sebi_circulars()` | Different product. Claude's own knowledge covers SEBI rules adequately for fund analysis. |
| `calculate_sip_returns()`, `project_returns()`, `suggest_portfolio()` | Advice/projection, not evidence. Out of scope — see §16. |
| `attribution()` | See §9. Should not exist as a standalone claim. |

**Possible sixth tool, Phase 3 only:** `list_disclosure_events(scheme_ids, since)` — returns
detected `ChangeEvent`s (manager changes, benchmark changes, TER changes, category changes,
addenda). Justified only because it spans documents *and* diffs and has no natural home in
the other four. Do not build it in MVP.

---

## 6. Provenance

Provenance is mandatory, and the failure mode is verbosity — a full provenance block on every
scalar would triple payload size and bury the data.

**Recommendation: two-tier provenance with a shared source table.**

Every response carries a `sources` dictionary once. Facts reference it by key and carry only
an epistemic tag:

```json
{
  "data": {
    "fund_manager":  {"v": "Rajeev Thakkar", "src": "s1", "k": "official"},
    "ter_direct":    {"v": 0.63, "src": "s1", "k": "official"},
    "ter_realised":  {"v": 0.71, "src": "c2", "k": "calculated"},
    "cagr_5y":       {"v": 21.4, "src": "c1", "k": "calculated"},
    "top10_pct":     {"v": 48.2, "src": "c3", "k": "calculated"},
    "hdfc_bank_contribution_aug": {"v": 0.31, "src": "c4", "k": "approximation",
                                   "caveat": "Assumes static holding across month; ignores intra-month trades."}
  },
  "sources": {
    "s1": {"type": "amc_factsheet", "document": "PPFAS Factsheet Aug 2026",
           "url": "https://amc.ppfas.com/...", "doc_date": "2026-08-31",
           "as_of": "2026-08-31", "page": 2, "sha256": "…", "retrieved": "2026-09-12T10:04Z"},
    "c1": {"type": "calculated", "method": "cagr_daily_nav",
           "inputs": ["amfi_nav_history"], "as_of": "2026-09-11",
           "params": {"plan": "direct_growth", "annualisation": 365}},
    "c4": {"type": "calculated", "method": "contribution_approx_v1",
           "inputs": ["portfolio_2026-07-31", "portfolio_2026-08-31"], "precision": "low"}
  }
}
```

**The five epistemic kinds** — `official` · `calculated` · `observed` (true of the disclosed
data, e.g. "held for 41 consecutive months") · `approximation` (has an unbounded error term)
· `inferred` (heuristic, e.g. active/passive from name). Claude must be able to see this
distinction at a glance, and the MCP's own tool descriptions should tell Claude to weight
`approximation` and `inferred` accordingly.

**Verbosity control:** a `provenance: "full"|"compact"|"none"` parameter, default `compact`.
Compact = the above. Full = expanded inline. None = for Claude's own iterative exploration,
where it will cite from a later full call.

---

## 7. Parsing strategy

| Format | Where it shows up | Approach |
|---|---|---|
| Delimited text | AMFI NAVAll + history **[verified]** | Hand-rolled parser. Stateful: section headers carry the taxonomy, blank lines separate blocks, AMC names appear as bare lines. ~80 lines of code. |
| XLSX | Most AMC portfolio files **[verified: PPFAS]** | `openpyxl` in read-only mode. **Do not assume a header row index** — scan for the row containing `ISIN` + `% to Net`. Then walk rows, tracking the current section label (`Equity & Equity related`, `(a) Listed…`, `Money Market Instruments`, `TREPS`, `Net Receivables`) to assign `asset_class`. |
| XLS (legacy BIFF) | Older files, some AMCs (PPFAS's combined file is `.xls` **[verified]**) | `xlrd<2.0` or LibreOffice headless conversion. Detect by magic bytes, **not by extension** — several AMCs serve HTML with an `.xls` name. |
| HTML index pages | AMC disclosure listings | `selectolax`/`lxml` + per-AMC CSS selectors in the adapter. |
| JS-rendered pages | AMFI `www` **[verified]**, ICICI Pru, some others | **Avoid at runtime.** Either find the backing JSON API once and hard-code it, or run a build-time Playwright job whose output is committed as a registry file. Never ship a headless browser in a `uvx` package. |
| PDF (text layer) | SID, KIM, factsheets, addenda | `pypdf` for text + `pdfplumber` for tables. Section detection by regex against the SEBI-mandated headings, which are standardised: `Investment Objective`, `Investment Strategy`, `Asset Allocation Pattern`, `Risk Factors`, `Fund Manager`, `Load Structure`, `Expenses of the Scheme`. |
| PDF (scanned) | Rare; a few small AMCs' old files | **Do not OCR in MVP.** Mark `parse_status: "unparseable_scanned"`, return the URL, and let Claude tell the user. Adding Tesseract to a `uvx`-installable package is a poor trade. |

**The three parsing rules that will save the project:**

1. **Sniff by magic bytes, never by extension or `Content-Type`.** Indian AMC sites lie about
   both, constantly.
2. **Every parser emits `parse_confidence` and a row-reconciliation check** — the sum of
   `% to Net Assets` must land within ~0.5 of 100. If it doesn't, flag the snapshot rather
   than silently serving a broken portfolio.
3. **Cache the raw bytes forever, keyed by sha256.** Re-parsing is free; re-fetching a file the
   AMC has since deleted is impossible. This single decision is what makes your historical
   dataset durable.

### 7.1 AMC adapter design

```python
class AMCAdapter(Protocol):
    amc_id: str
    def list_documents(self, doc_type: DocType, since: date) -> list[DocumentRef]: ...
    def fetch(self, ref: DocumentRef) -> bytes: ...
    # parsing is shared; only discovery is AMC-specific
```

Most adapters are 30–60 lines. Seed them from the AMFI registry page **[verified]** and build
in dependency order: PPFAS, HDFC, ICICI Pru, SBI, Nippon, Kotak, Axis, Mirae, Motilal, DSP,
Quant, UTI, ABSL, Canara Robeco, Franklin, Tata, Edelweiss, Bandhan, Invesco, HSBC — that's
~85% of industry AUM and covers essentially every fund anyone will ask about.

**Add a generic fallback adapter** that scrapes any AMC's disclosure page for links matching
`(xls|xlsx|pdf)` with a date-like filename. It will work for perhaps half the long tail and
degrade honestly for the rest.

---

## 8. Architecture: cache and history

### 8.1 The three options, assessed

| | **A. Fetch live every call** | **B. Lightweight cache** | **C. Local historical dataset** |
|---|---|---|---|
| Complexity | Lowest | Low–medium | Medium |
| First-call latency | 10–120 s (PDF/XLSX parse) | Same cold, ms warm | Same cold, ms warm |
| Freshness | Perfect | TTL-dependent | TTL-dependent |
| Reliability | **Poor** — any AMC outage = analysis fails | Good | **Best** |
| Load on sources | **Bad** — abusive under repeated queries | Fine | Fine |
| Historical analysis | **Impossible** — cannot see what a site no longer hosts | Partial | **Yes** |
| Storage | 0 | ~100 MB | 2–10 GB for the full universe; ~50 MB for a focused set |

### 8.2 Recommendation: **C, but arrived at incrementally via B**

The decisive argument is not speed — it is that **options A and B cannot deliver your §7
requirements at all.** "Has the fund drifted from its original mandate?" and "when did the
strategy start changing?" are questions about data that AMC websites will eventually stop
hosting. If the MCP does not retain it, the capability does not exist. A cache that never
evicts raw source bytes *is* a historical dataset. So C is not extra infrastructure; it is B
with the eviction policy deleted.

**Concretely:**

- **SQLite** at `~/.indian-mf-mcp/store.db` + a content-addressed blob directory for raw
  documents. Zero-config, `uvx`-compatible, no server, no daemon.
- **Lazy by default.** Nothing is downloaded until a fund is first asked about. The first
  `get_fund_portfolio(..., history="60M")` on a new fund is slow (~1–3 min); every subsequent
  call is instant. Say so in the tool description so Claude can warn the user.
- **Eager NAV.** `NAVAll.txt` is 1.5 MB **[verified]**. Fetch it daily and archive every copy.
  This is the cheapest high-value thing in the entire system — it's how you get point-in-time
  category history, universe membership, and scheme renames.
- **TTLs:** NAV 1 day · portfolios 7 days (they only change monthly, but AMCs file late) ·
  factsheets 7 days · SID/KIM 90 days · AMC registry 30 days.
- **Optional `mf-mcp backfill --amc ppfas --from 2019` CLI** for users who want history warmed
  in advance.
- **Never evict raw blobs.** Parsed tables can be regenerated; source documents cannot.

The `uvx mutual-fund-mcp` install story survives intact — SQLite and a blob dir are just files.

---

## 9. The portfolio change engine

**Verdict: yes, this should be a core capability — with explicitly stated limits.**

### 9.1 What is reliably computable

Given two ISIN-keyed month-end snapshots **[verified: ISIN present in real files]**:

| Signal | Reliability |
|---|---|
| NEW / EXITED position | **High** — ISIN set difference |
| INCREASED / REDUCED / UNCHANGED | **High on quantity**, once corporate actions are handled |
| Δ quantity, Δ %NAV, Δ market value | **High** |
| Sector allocation change | **High** — industry strings are consistent across AMCs **[verified]** |
| Concentration change (top-N, HHI, effective N) | **High** |
| Cash allocation change | **High** |
| Holding persistence / conviction streaks | **High** — the most valuable output, and uniquely yours |
| Market-cap allocation change | **Medium** — depends on using the *contemporaneous* cap list |

**Compare quantity, not %NAV.** A position's %NAV moves when the stock moves or when the fund
receives inflows, with no trade at all. Quantity change is the only clean trade signal.
`Δqty ≈ 0` with `Δ%NAV ≠ 0` is *price/flow drift*, and should be labelled as such — it is a
distinct and analytically useful category that most commercial tools get wrong.

### 9.2 What will break it, and the mitigations

| Hazard | Effect | Mitigation |
|---|---|---|
| **Stock split / bonus** | Quantity jumps with no trade → false "INCREASED" | Detect: qty ratio ≈ a simple integer/rational ratio **and** %NAV roughly unchanged → flag `corporate_action_suspected`, don't assert a trade. A proper fix needs an external corporate-action feed — out of MVP scope; **flag, don't guess**. |
| **Merger / amalgamation** | Old ISIN exits, new ISIN appears → false "EXIT + NEW" | Same month, similar value, flagged as `possible_corporate_action`. |
| **Company name change** | Handled correctly *if* ISIN is stable; ISIN also changes on some restructurings | ISIN-primary, name-similarity as a secondary check. |
| **ISIN change** | False exit+entry | Name-similarity fallback matching, flagged low-confidence. |
| **Derivatives** | Notional vs market value, long/short signs, hedges | Segregate into their own asset class. **Never net them into equity exposure** without saying so. |
| **Fortnightly vs monthly cadence** | Debt schemes disclose fortnightly; some AMCs publish mid-month equity too | Compare only like-cadence snapshots; record `disclosure_type`. |
| **Late filings** | AMFI target is ~10th of following month **[reported]**; some AMCs are later | Never infer "no disclosure" from absence; retry and record `expected_but_missing`. |
| **Missing month in archive** | Gap in the series | Report the gap explicitly. Do **not** interpolate. A 2-month change presented as a 1-month change is a lie. |
| **Inflows/outflows** | Large inflow reduces every %NAV with zero trades | Always report Δqty alongside Δ%NAV; report AUM change as context. |
| **Segregated portfolios (side-pocketing)** | Separate scheme codes appear | Detect and surface; don't merge. |

**Output design:** every change row carries `{isin, name, action, delta_qty, delta_pct_nav, delta_value, confidence, flags[]}`.
`confidence` is not decoration — it's the mechanism by which Claude avoids asserting a trade
that was actually a bonus issue.

---

## 10. Performance attribution — the honest answer

**True attribution is not possible from Indian public MF disclosure. Do not build it.**

Brinson-style attribution requires portfolio weights *and* benchmark weights *and* returns for
both, at matched frequency, with intra-period trade handling. You have:

- Portfolio weights: month-end only ✓ (coarse)
- Benchmark constituent weights: ✗ (licensed)
- Transaction prices and dates: ✗ (never disclosed)
- Intra-month trades: ✗ (invisible by construction)

| Claim | Status |
|---|---|
| Holding-level contribution approximation (`avg weight × stock return over the period`) | 🟡 **Approximation.** Ship it, labelled `k: "approximation"`, with the static-holding caveat in the payload. Requires stock price data — a dependency you may not want in MVP. |
| Sector allocation *changes* over time | 🟢 **Fact.** Ship it. |
| Concentration and its evolution | 🟢 **Fact.** Ship it. |
| "Dependence on top holdings" as a *structural* statement | 🟢 **Fact** (weights), not a return decomposition. |
| Cash drag | 🟡 Approximable from disclosed cash % and index return. Label it. |
| Brinson allocation vs selection effects | ⚫️ **Do not emit.** |
| "This fund's alpha came from stock selection in financials" | ⚫️ **Do not emit.** |
| Whether past outperformance is repeatable | **Claude's job**, from portfolio evidence + process documents + rolling-return consistency. |

The strongest honest substitute for attribution — and it is genuinely strong — is the combination
you *can* deliver: **rolling-return consistency** (is alpha broad-based or episodic?) plus
**holding persistence** (are the big positions long-held convictions or recent bets?) plus
**concentration** (how few names is the result riding on?). Claude can reason from that trio to
a defensible view on repeatability without any false precision. Frame this explicitly in the
tool descriptions so Claude reaches for it instead of asking for attribution.

---

## 11. Source-routing strategy

Your proposed hierarchy, corrected:

| Fact | Primary | Secondary | Fallback | Notes |
|---|---|---|---|---|
| NAV (current) | AMFI `NAVAll.txt` | AMC site | `mfapi.in` | ✅ your version was right |
| NAV (historical) | AMFI `DownloadNAVHistoryReport_Po.aspx` | local archive | `mfapi.in` | ✅ |
| Scheme identity, category | AMFI `NAVAll.txt` | SID | — | **Correction:** you listed AMFI for "scheme information" generally; for *taxonomy* AMFI is not just acceptable, it is the canonical machine-readable source. |
| Holdings | **AMC monthly portfolio file** | AMFI registry link | half-yearly portfolio in annual report | **Correction:** you wrote "AMC / AMFI disclosure" as if AMFI hosts portfolios. **It does not** — it hosts links. **[verified]** |
| Objective, strategy, restrictions | SID | KIM | factsheet summary | ✅ |
| Benchmark | Factsheet / portfolio file footer **[verified]** | SID | — | **Correction:** SID is authoritative but stale between revisions; the monthly file is both authoritative and current. Prefer it, cross-check against SID. |
| Fund manager | Factsheet (monthly, dated) | SID | AMC website | **Correction:** SID lags badly. Factsheet first. |
| Manager *changes* | **Addendum/notice** | factsheet diff | — | Addendum is the legal event; the diff is corroboration. |
| TER | AMC daily TER disclosure | AMFI TER page | factsheet | **Correction:** AMFI's TER page is SPA-only **[verified]**; AMC files are more reliably fetchable. Also compute the realised spread from NAV (§3.8). |
| Exit load, turnover | Factsheet | SID | — | |
| AUM | AMFI monthly report **[verified]** (category) + factsheet (scheme) | — | — | |
| Market-cap classification | AMFI half-yearly cap list | — | — | Must be version-stamped. |
| Category averages | **Computed in-MCP** from universe NAV | — | — | No free authoritative source exists. |
| Benchmark index level | Licensed index data | **passive-fund NAV proxy** | category median | See §3.4. Always label the proxy. |
| Regulations | SEBI | AMFI | — | Minimal ingestion; Claude knows this domain. |

---

## 12. Where each calculation lives

**In the MCP** (deterministic, expensive, methodology-critical): CAGR and trailing returns ·
rolling-return distributions · volatility, beta, Sharpe, Sortino, IR, alpha · drawdown
detection, duration, recovery · up/down capture · stress-window returns · category ranks and
percentiles · portfolio diffs and persistence · concentration and HHI · sector/cap/asset
aggregation · turnover proxies · realised direct-vs-regular cost spread · corporate-action
flagging · portfolio overlap between funds (set intersection weighted by %NAV — cheap,
mechanical, and needed for the comparison use case).

**In Claude** (judgement, synthesis, contradiction-spotting): does the portfolio match the
stated philosophy · is the process credible and repeatable · is alpha persistent or episodic ·
what is the fund's role in a portfolio · what are the real risks · has the strategy drifted ·
which of several funds is more attractive and for whom · is the manager change material ·
reconciling contradictions between documents · deciding what further evidence to request.

**The boundary rule:** *if two competent analysts with the same data would get the same number,
it belongs in the MCP; if they would reasonably disagree, it belongs to Claude.*

---

## 13. Claude interaction examples

### 13.1 "Analyze Parag Parikh Flexi Cap Fund using the first-principles framework."

```
1. resolve_fund("Parag Parikh Flexi Cap")
   → scheme_id=PPFCF, direct_growth plan code 122639, ISIN INF879O01027, inception 2013-05-28

2. get_fund_profile(["PPFCF"])
   → identity, mandate excerpts (SID pp. 12–18), benchmark Nifty 500 TRI,
     TER 0.63/1.31, managers + managing-since, document index

3. get_fund_performance(["PPFCF"], period="since_inception",
                        comparators=["benchmark","category"],
                        metrics=["trailing","rolling","risk","drawdown","stress"])
   → CAGR ladder, 3Y rolling distribution + % windows beating category,
     Sharpe/Sortino/beta, every >10% drawdown with recovery times,
     COVID / 2022 / 2024 stress windows

4. get_fund_portfolio(["PPFCF"], as_of="latest", compare_to="prev_month",
                      history="60M",
                      sections=["holdings","allocations","concentration",
                                "changes","persistence"])
   → 30 holdings, sector + cap + cash mix, top-10 = 48%,
     Aug-vs-Jul changes, 5-year persistence table

5. get_document(scheme_id="PPFCF", doc_type="SID",
                query="investment strategy, sell discipline, overseas allocation")
   → the 4 relevant pages, verbatim, with page anchors
```
Five calls. Claude then writes the analysis — including the parts the MCP deliberately refused
to compute, such as whether the actual portfolio matches the stated philosophy.

### 13.2 "Compare PPFAS, HDFC and JM Flexi Cap on consistency of process."

```
resolve_fund(["Parag Parikh Flexi Cap","HDFC Flexi Cap","JM Flexicap"])
get_fund_performance([A,B,C], period="10Y", metrics=["rolling","risk"], rolling_windows=[3,5])
get_fund_portfolio([A,B,C], history="60M", sections=["persistence","allocations","concentration"])
get_document(each, doc_type="SID", query="investment strategy")
```
Four calls, no `compare_funds` tool, because the fund parameter is a list. Claude compares
rolling-return dispersion, holding persistence, and stated-vs-actual strategy itself.

### 13.3 "What changed in HDFC Flexi Cap's portfolio over the last 12 months?"

```
get_fund_portfolio(["HDFC_FLEXI"], history="12M",
                   sections=["changes","allocations","concentration","persistence"])
```
One call. Returns 12 monthly diffs, sector drift, concentration trajectory, and the persistence
table — with `corporate_action_suspected` flags where quantity ratios look like splits.

### 13.4 "Did the manager change, and what happened to performance after?"

```
get_fund_profile([X], sections=["managers"])           # current + managing-since
get_document(X, doc_type="ADDENDUM", query="fund manager change")   # the legal event
get_fund_performance([X], period="10Y", metrics=["rolling","risk"])
```
Claude splits the rolling-return series at the change date itself. **Honest caveat to surface:**
in MVP the MCP may only have the *current* manager and a `managing_since` date, not a full
history. Phase 3 adds `ManagerAssignment` reconstruction.

### 13.5 A query the MCP should refuse to answer directly

> "How much of the fund's alpha came from stock selection versus sector allocation?"

The MCP returns no attribution decomposition. `get_fund_portfolio` returns concentration and
persistence; `get_fund_performance` returns rolling consistency. Claude answers with a reasoned
assessment and an explicit statement that a true decomposition is not computable from Indian
public disclosure. **This is the system working correctly**, and the tool descriptions should
say so, so Claude doesn't go looking for a tool that isn't there.

---

## 14. MVP roadmap

### Phase 1 — Evidence spine (2–3 weeks)
*Goal: `resolve_fund` + `get_fund_performance` work for the entire universe.*
- AMFI `NAVAll.txt` daily ingest + archive; taxonomy parsed from section headers
- AMFI historical NAV backfill, chunked by `mf=<amc>` × month
- SQLite store + content-addressed blob dir
- Full analytics engine (trailing, rolling, risk, drawdown, stress)
- Category statistics from the universe; **benchmark proxy via passive-fund NAV**
- `resolve_fund`, `get_fund_performance`
- **Deliverable:** Claude can do pillars D and E for any Indian mutual fund, with provenance.

### Phase 2 — Portfolio spine (3–5 weeks) ← *this is where the product becomes differentiated*
- AMFI disclosure-registry scrape → AMC adapter bootstrap **[verified available]**
- Adapters for the top 20 AMCs by AUM + generic fallback
- XLSX/XLS/PDF portfolio parsers with section awareness and the 100%-reconciliation check
- Normalised holdings store; AMFI cap-list join (version-stamped)
- Change engine with corporate-action flagging; persistence computation
- `get_fund_portfolio`
- **Deliverable:** pillars F and most of A. "What changed, and what has it always held?"

### Phase 3 — Document intelligence (3–4 weeks)
- SID/KIM/factsheet fetching; AMFI `spages/<id>.pdf` mapping discovery **[verified these exist]**
- Section-aware PDF extraction against SEBI-standard headings
- `get_fund_profile`, `get_document`
- Manager extraction from factsheets; `ManagerAssignment` reconstruction by diffing archives
- TER capture and history
- **Deliverable:** pillars A, B, C, H. The framework is now fully served.

### Phase 4 — Change monitoring & polish (2–3 weeks)
- `ChangeEvent` detection: manager, benchmark, TER, category, mandate revisions
- Addenda ingestion
- Optional `list_disclosure_events`
- Portfolio overlap between funds; `mf-mcp backfill` CLI; packaging for `uvx`
- Adapter health checks that alert when an AMC site changes shape

**Sequencing rationale:** Phase 1 is a commodity that several people have already built — but
it is the substrate everything else needs, and it is fast. Phase 2 is the actual moat and
should not be delayed. Phase 3 is the highest-effort, lowest-certainty work, which is why it
comes after you have already shipped something useful.

---

## 15. Risks and limitations

**State these in the README and in tool descriptions, not just here.**

| Risk | Reality | Handling |
|---|---|---|
| **Historical portfolios are shallow** | PPFAS: 2019–2026 for a 2013 fund **[verified]**. Most AMCs keep 3–7 years. | State the actual coverage window in every response. Never let "no data" read as "no change". |
| **Category history is unavailable retrospectively** | `NAVAll.txt` gives today's category only | Archive `NAVAll.txt` from day 1. Accept that pre-adoption history is lost. |
| **Benchmark TRI is not freely available** | Genuine gap **[unknown — verify NSE licensing]** | Passive-fund proxy + category median, always labelled. Never present a proxy as the index. |
| **Market-cap lists are point-in-time** | Cutoffs move every 6 months | Version-stamp; never reclassify history with today's list. |
| **45 AMC sites, all different, all changing** | Adapters will break | Generic fallback + health checks + graceful degradation. Report `source_unavailable`, never guess. |
| **Scraping restrictions / ToS** | Disclosures are legally mandated public documents, which is favourable — but rate limits and robots directives still apply **[unknown per-AMC]** | Polite rate limiting, honest User-Agent, aggressive caching, respect robots.txt. Review each AMC's terms before shipping. |
| **Corporate actions corrupt change detection** | Splits/bonuses/mergers produce false trade signals | Flag, don't assert. §9. |
| **Attribution is impossible** | Structural | Refuse to emit it. §10. |
| **Stale data** | AMCs file late; NAV lags a day | Every payload carries `as_of` and `retrieved_at`; never hide staleness. |
| **False precision** | The single biggest credibility risk | Epistemic tags on every fact. Round appropriately. A Sharpe of 1.2 is not 1.2047. |
| **AMFI's `www` site is SPA-only** | **[verified]** — breaks naive scraping | Build on `portal.amfiindia.com`. Do build-time discovery for anything SPA-bound. |
| **Regulatory positioning** | An MCP producing fund analysis edges toward investment advice | §16. |
| **Plan/option confusion** | Regular-vs-Direct is ~100 bps/yr | Default to Direct/Growth; state the plan in every performance payload. |
| **Survivorship bias** | `NAVAll.txt` lists live schemes; merged/wound-up funds vanish | Archive daily and retain delisted schemes with `active=false`. Category averages computed from live schemes only are biased upward — **say so in the payload**. |

---

## 16. Scope: research vs execution

**Recommendation: execution is completely out of scope, and should stay out permanently.**

| Layer | In scope? |
|---|---|
| Research — retrieve, parse, normalise, preserve | ✅ Core |
| Analysis — deterministic computation | ✅ Core |
| Monitoring — detect disclosure changes | ✅ Phase 4 |
| Portfolio tracking — user's own holdings (CAS import) | ⚠️ Adjacent, defensible later, but it changes the product from "research layer" to "personal finance tool" with a completely different privacy and trust posture. Keep separate. |
| Execution — transact, place orders | ❌ **Never.** |
| Recommendation / scoring / ranking | ❌ Not an MCP capability. This is Claude's reasoning, exposed and examinable. |

Practical guardrails: read-only by construction, no credentials anywhere, no order APIs, no
broker integrations. Tool descriptions should frame outputs as *evidence for analysis*, and the
server should not emit buy/sell language. A short disclaimer in the server instructions
(research and educational use; not investment advice; verify against source documents, whose
URLs are supplied) is appropriate and cheap.

---

## 17. Final recommended design

```
                          Claude
                             │
        ┌────────────────────┴────────────────────┐
        │            MCP tool surface             │
        │   resolve_fund                          │
        │   get_fund_profile                      │
        │   get_fund_performance                  │
        │   get_fund_portfolio                    │
        │   get_document                          │
        │   (Phase 4: list_disclosure_events)     │
        └────────────────────┬────────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        │   Provenance wrapper                    │
        │   two-tier: facts + shared source table │
        │   epistemic tags: official / calculated │
        │   / observed / approximation / inferred │
        └────────────────────┬────────────────────┘
                             │
   ┌──────────────┬──────────┴──────────┬──────────────────┐
   │  Analytics   │   Change engine     │  Doc extraction  │
   │  returns     │   ISIN diff         │  section-aware   │
   │  risk        │   persistence       │  PDF → passages  │
   │  rolling     │   corp-action flags │                  │
   │  drawdown    │   concentration     │                  │
   │  category    │   overlap           │                  │
   └──────────────┴──────────┬──────────┴──────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        │   Normalisation  (taxonomy, ISIN keys,  │
        │   industry vocab, cap-list versioning)  │
        └────────────────────┬────────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        │   Parsers   text/CSV · XLSX · XLS · PDF │
        │             magic-byte sniffing         │
        │             reconciliation checks       │
        └────────────────────┬────────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        │   Store   SQLite + content-addressed    │
        │           blob dir. Raw bytes NEVER     │
        │           evicted → this IS the history │
        └────────────────────┬────────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        │   Source routing + per-AMC adapters     │
        │   (bootstrapped from AMFI's registry)   │
        └────────────────────┬────────────────────┘
                             │
   ┌─────────────┬───────────┴───────┬─────────────┬────────┐
   │ portal.amfi │  ~45 AMC sites    │ AMFI cap    │ SEBI   │
   │ NAVAll      │  portfolios       │ lists       │ (thin) │
   │ NAV history │  factsheets       │             │        │
   │ monthly rpt │  SIDs / addenda   │             │        │
   │ spages PDFs │  annual reports   │             │        │
   └─────────────┴───────────────────┴─────────────┴────────┘
```

**Everything in that diagram lives inside the MCP package.** No backend, no hosted service, no
API keys. `uvx mutual-fund-mcp` installs a Python package that writes to `~/.indian-mf-mcp/`.
The only thing a user must accept is that the first deep query on a new fund is slow while the
cache warms.

### 17.1 The five design commitments

1. **Five tools, list-valued fund parameters, `sections`/`metrics` filters.** Comparison is a
   parameter, not a tool.
2. **The MCP computes; it does not conclude.** No scores, no ratings, no recommendations, no
   attribution.
3. **Provenance on every fact, with an epistemic tag.** `official` ≠ `calculated` ≠
   `approximation` ≠ `inferred`, and Claude can see which.
4. **Raw source bytes are never deleted.** The cache is the historical dataset.
5. **Unavailable data is reported as unavailable.** No interpolation, no proxying without a
   label, no silent degradation.

### 17.2 What I'd build first, concretely

If you want a single week that proves the thesis: **Phase 1 for NAV/analytics, plus the PPFAS
adapter only.** That gets you end-to-end — resolve → performance → portfolio → change engine →
provenance — on one fund with clean data **[verified: PPFAS files parse cleanly, archive back
to 2019]**. Run your own §18 test query against it. If Claude's analysis of PPFAS Flexi Cap
from that evidence is materially better than what it produces unaided, the architecture is
validated and the remaining work is adapter grind. If it isn't, you've learned that cheaply.

---

## Sources

- [AMFI NAV (all schemes)](https://portal.amfiindia.com/spages/NAVAll.txt) · [AMFI historical NAV report](https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?tp=1&frmdt=01-Aug-2026&todt=03-Aug-2026) · [AMFI portfolio-disclosure registry](https://www.amfiindia.com/online-center/portfolio-disclosure) · [AMFI monthly reports](https://www.amfiindia.com/research-information/amfi-monthly) · [AMFI TER page](https://www.amfiindia.com/ter-of-mf-schemes) · [AMFI scheme details](https://www.amfiindia.com/otherdata/scheme-details) · [AMFI-hosted SID example](https://portal.amfiindia.com/spages/13937.pdf)
- [PPFAS portfolio disclosures](https://amc.ppfas.com/downloads/portfolio-disclosure/) · [HDFC monthly portfolio](https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio) · [SBI portfolios](https://www.sbimf.com/en-us/portfolios) · [DSP portfolio disclosures](https://www.dspim.com/mandatory-disclosures/portfolio-disclosures)
- [SEBI Master Circular for Mutual Funds](https://www.sebi.gov.in/sebi_data/attachdocs/1337083696184.pdf) · [SEBI formats](https://www.sebi.gov.in/sebi_data/commondocs/may-2023/Formats%20for%20Master%20Circular%20for%20Mutual%20Funds%20as%20on%20March%2031,%202023_p.pdf)
- [mfapi.in docs](https://www.mfapi.in/docs/) · [mftool](https://mftool.readthedocs.io/) · [historical-mf-data](https://github.com/captn3m0/historical-mf-data)
- [FinStack MCP](https://github.com/finstacklabs/finstack-mcp) · [mftool-MCP](https://mcpmarket.com/server/mftool) · [cf-stock-mcp-server](https://github.com/imdurgadas/cf-stock-mcp-server)
- [AMFI stock categorisation coverage](https://matasec.substack.com/p/amfis-stock-categorization-update-8fc) · [AMFI expense ratio explainer](https://www.amfiindia.com/investor/knowledge-center-info?zoneName=expenseRatio) · [TER daily disclosure requirement](https://cafemutual.com/news/industry/13208-fund-houses-to-disclose-scheme-ter-daily)
