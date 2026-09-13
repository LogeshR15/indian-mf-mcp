# Contributing

Thanks for considering a contribution. This project has unusually strong opinions about data
integrity, so please read the rules below before writing code — they explain why several
things that look like obvious improvements would be rejected.

## Ground rules

### 1. Never fabricate, never fill gaps

If a fact isn't available, the answer is that it isn't available. Do not substitute a
plausible value, interpolate across a gap, or fall back to a less authoritative source without
labelling it. A missing number is a fact about the world; a fabricated one is a bug that
silently propagates into someone's reasoning about their money.

### 2. Provenance is not optional

Every value surfaced through an MCP tool carries a `k` (kind) tag:

| Kind | Meaning |
|---|---|
| `official` | Stated verbatim in a regulator-mandated disclosure |
| `calculated` | Computed by us from official inputs, deterministically |
| `observed` | Derived from data we archived over time |
| `approximation` | Has a real, unbounded error term |
| `inferred` | A heuristic that is usually right |

If you add a fact, tag it honestly. `approximation` and `inferred` exist so a model can
discount them — mislabelling one as `calculated` defeats the entire design.

### 3. Reconciliation gates ingestion

A portfolio whose weights don't sum to 100% is **rejected**, not loaded. If you find yourself
loosening a reconciliation check to make a file import, stop: either the parser is
misreading the file, or the file genuinely doesn't reconcile and shouldn't be trusted.

### 4. We do not evade access controls

**No spoofed User-Agents. No proxy or UA rotation. No TLS/JA3 fingerprint spoofing. No
CAPTCHA solving. No stealth browser plugins.**

Always send the project's honest `config.USER_AGENT`. If an AMC blocks it, the legitimate
moves are to look for infrastructure that *isn't* blocked (see below), or to document it as
blocked and move on.

This is not hypothetical. Canara Robeco's files parse perfectly and its WAF accepts a spoofed
browser UA — and we skip it anyway. That decision stands.

### 5. No headless browser at runtime

Playwright is a **build-time discovery tool only** — use it once to find a backing endpoint,
then reproduce that request with plain `httpx` in the adapter. An adapter that drives a
browser at runtime won't be merged.

---

## Adding an AMC adapter

This is the highest-value contribution available and each one is self-contained. ~31 AMCs
still have no adapter.

### Before you start

Find your AMC's registered disclosure URL in AMFI's own registry at
<https://www.amfiindia.com/online-center/portfolio-disclosure> — AMFI publishes a per-AMC
`amc_monthly_portfolio_disclosure` field, and it is often more current than whatever a search
engine surfaces. Then read [docs/amc-coverage.md](docs/amc-coverage.md) and find the
closest-shaped AMC to yours.

### Step 1 — Map the site structure first

Do not start writing an adapter until you understand how the site actually serves documents.
Answer these four questions:

1. **What renders the listing?** Server-side HTML, an inline JS array literal, an AJAX/JSON
   endpoint, or a client-side SPA?
2. **Where do the files physically live?** The same host, or a separate CDN / S3 bucket?
3. **How do URLs encode scheme and date?** Is the URL computable, or must it be discovered?
4. **How is history reached?** Not just the latest month — a year selector, a query parameter,
   or per-month iteration?

Question 4 matters more than it looks. Two existing adapters shipped with latest-month-only
endpoints, a permanent capability limit that came from not asking early.

### Step 2 — Try the cheap discovery routes in order

Most AMCs fall to something much simpler than a browser:

1. **Static links in the page HTML.** Sometimes the whole archive is right there.
2. **An inline `<script>` or a served static JS asset.** Several adapters needed no browser at
   all because the request shape — endpoint, parameters, headers — was spelled out in an asset
   the site already serves. Read the JS before reaching for Playwright.
3. **Embedded page data.** Server-rendered Next.js apps often carry the entire listing in
   `__NEXT_DATA__`.
4. **A computable URL.** If there's no usable listing, the file URL may still be derivable
   from (scheme, as-of date) and verifiable by probing.
5. **One-time Playwright capture.** Last resort, and only to *find* the endpoint.

### Step 3 — If the portal is blocked, check the file host

**The portal is not the product.** An AMC's investor portal and its document host are
frequently separate infrastructure with separate rules. One AMC's `www` returns
`403 Access Denied` on every path including its homepage, while its file bucket serves the
same honest User-Agent a clean `200`.

So before recording an AMC as blocked, check whether a separate `files.` / `cdn.` / `assets.`
host or an S3/CloudFront bucket exists, and whether it answers. A public search engine is a
legitimate way to find one real file URL and learn the naming convention from it.

Beware these traps, all of which have produced a wrong "blocked" verdict here:

- A **404 status on a 200 SPA shell** is cosmetic, not a block.
- An **anti-bot script** may guard only the shell, never the documents.
- An **S3 bucket with `ListBucket` denied** returns `403` for missing keys, not `404` — so
  `403` can mean "no such file" rather than "no access".
- A page that looks empty may be **click-gated**, with content loaded only after interaction.
- An **encrypted API tier** may sit alongside a plaintext one; check for an encryption-status
  flag in the site's bundled JS before concluding payloads are encrypted.

### Step 4 — Write the adapter

Create `src/indian_mf_mcp/ingest/amc_adapters/<amc>.py`:

```python
class YourAMCAdapter:
    amc_id = "amc-<slug>"

    def list_documents(self, doc_type, since, scheme_hint=None, client=None) -> list[DocumentRef]:
        ...

    def fetch(self, ref, client=None) -> bytes:
        ...
```

**Write a real module docstring.** Record how discovery was ground-truthed, the exact endpoint
or URL convention, how history is reached, and every quirk you hit. These docstrings are the
project's institutional memory — the next person to touch your adapter when the AMC changes
its site will depend on yours. Look at `hdfc.py` or `sundaram.py` for the expected depth.

Most AMCs need **no changes to the shared parser**. If yours seems to, first check whether the
quirk can be repaired inside your own adapter instead. Only genuinely general improvements
belong in `xlsx_portfolio.py` or `combined_workbook.py`, and they must not regress other AMCs.

### Step 5 — Register it

Add to `ADAPTERS` in `registry.py`, and to the `--amc` choices in `cli.py`. If your AMC
publishes one combined workbook with a sheet per scheme, supply a `sheet_resolver`.

### Step 6 — Test it with real data

Two tests, both required:

**Golden fixture test** (`tests/unit/test_xlsx_portfolio_parser_<amc>.py`) — commit one real
downloaded file to `tests/fixtures/` and assert reconciliation passes, holdings are extracted,
and a specific real holding has the right ISIN and weight.

**Live smoke test** (`tests/integration/test_<amc>_live_smoke.py`) — mirror an existing one;
skip when the network is unavailable, and assert an end-to-end ingest reconciles.

Use `tmp_path` databases. Never touch the developer's real store.

```bash
uv run pytest -q
```

### A blocked AMC is a real result

If an AMC genuinely can't be reached without evading a control, **say so with evidence** —
which hosts you tried, what each returned, what the response bodies looked like. A
well-documented blocked entry saves the next person the same investigation, and
`docs/amc-coverage.md` exists for exactly this. Don't force an adapter that can't actually
fetch files.

---

## Other ways to help

- **TER and fund-manager extraction** from factsheets — currently reported as unavailable.
- **Change monitoring** — the diffing engine exists; the monitoring layer on top doesn't.
- **Re-testing blocked AMCs.** Five entries once recorded as blocked turned out to be
  misdiagnosed. Others on that list may be too.

## Pull requests

- One AMC or one concern per PR.
- Run `uv run pytest -q` before opening.
- Explain *how you verified against real data*, not just that tests pass.
- Say plainly what your change does **not** cover — known gaps are useful, quiet ones aren't.

## Licence

**No licence has been chosen yet.** Until the repository owner adds one, default copyright
applies: the code can be read but not freely reused, and contributions cannot be formally
accepted. If you're interested in contributing, please open an issue asking for a licence
decision first.
