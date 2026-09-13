# AMC coverage and source forensics

Per-AMC notes on how each Asset Management Company's portfolio disclosures are discovered,
what is blocked and why, and the quirks each one forced into the code.

This is a working log, not marketing. It is deliberately specific: the hard part of this
project is not parsing spreadsheets, it is *getting* them, and the details below are the
accumulated result of investigating each AMC live. If you are adding an adapter, read
[CONTRIBUTING.md](../CONTRIBUTING.md) first, then find the closest-shaped AMC below and copy
its approach.

**A standing caution about the word "blocked."** Five of the fifteen AMCs once recorded here
as blocked turned out not to be. Each had been judged on the wrong surface — an investor
portal rather than the file host, a browser route that was never required, a page whose
content is click-gated, or an anti-bot script that only guarded a single-page-app shell. Treat
every entry below as a snapshot of one investigation, not a settled fact.

---

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
  - **AMC coverage today (22): Axis, Bank of India, Baroda BNP Paribas, DSP, Franklin
    Templeton, HDFC, ICICI Prudential, Kotak Mahindra, LIC, Mirae Asset, Motilal Oswal, Navi,
    Nippon India, PPFAS, quant, SBI, Sundaram, Tata, Taurus, Union, UTI, and Zerodha** (see
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
  - **Ten AMCs remain blocked, down from fifteen — and five of those fifteen were
    misdiagnosed, not blocked.** HDFC, Kotak, Axis, ICICI Prudential and quant are all live as
    of 2026-09-13 (details below). That is a 1-in-3 error rate in the original triage, and the
    errors were not random: each came from testing the wrong surface. HDFC and Kotak were
    judged on their *investor portals* rather than their file hosts; Axis was judged on a
    *browser route* that was never required; quant was judged on the *wrong page*, one whose
    content is click-gated; ICICI was judged on an anti-bot script that only ever guarded the
    SPA shell. **The portal is not the product.** Every verdict below was reached the same way
    and none should be trusted until re-tested against the file host, any static JS asset the
    site already serves, and AMFI's own registered URL.
  - **Still blocked, by failure mode:**
    - *Commercial WAF on the whole domain* — **Invesco** and **WhiteOak Capital**
      (CloudFront/AWS-WAF; Invesco's India business may also have been rebranded, possibly
      making it moot) and **Edelweiss** (Akamai edge WAF on its portfolio API specifically).
    - *Headless-JS fingerprinting* — **PGIM India**: the real API is same-origin but reachable
      only after client-side JS the site fingerprints and blocks in Playwright. Worth
      re-testing the way Axis was solved — by reading its bundled JS for the request shape
      instead of driving a browser at all.
    - *No discoverable disclosure page* — **HSBC**. AMFI does register a URL for it, which the
      original attempt may not have had.
    - *Application-layer payload encryption* — **Bandhan**, **JM Financial** and **Mahindra
      Manulife** all return a real `200 OK` whose `{"data"/"payload": "<base64>"}` body decodes
      to high-entropy ciphertext rather than JSON. Replaying a captured request fails;
      decrypting would mean reverse-engineering each site's client-side crypto, which is out of
      scope. The repetition across three unrelated AMCs suggests a shared fintech backend
      vendor. **But see the Axis finding below**: Axis's own bundled JS exposes an
      `API_ENCRYPTION_STATUS_CMS: "none"` flag marking its CMS tier as plaintext while its
      transactional tier is encrypted. An AMC running both tiers would look encrypted if only
      the transactional one was probed, so these three deserve a re-test before the verdict
      stands.
    - *Policy, not technology* — **Canara Robeco**'s discovery works cleanly (static links,
      exact 100% reconciliation, zero parser changes), but its WAF rejects the project's honest
      User-Agent while accepting a spoofed browser one. Asked the user rather than deciding
      unilaterally; **the decision was to skip it** and keep spec.md's honest-UA policy intact.
      This is the one entry that is a choice rather than an obstacle.
    - *Sandbox artifact, not a real blocker* — **Aditya Birla**'s discovery endpoint is found
      and documented; its file host is blocked by *this sandbox's* network policy and should
      work in a normal environment.
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
  - **quant** is driven by a classic ASP.NET WebForms *PageMethod* —
    `POST /statutorydisclosures.aspx/displaydisclouser` with `{"id": "<year>", "cat": "MONTHLY
    PORTFOLIO"}`, returning `{"d": "<ul>...</ul>"}`, one `<li><a>` per month. Despite being
    WebForms it needs no cookies, viewstate or session at all, and history comes from looping
    the `id` param over years (verified live back to 2018). Its filenames are hand-uploaded and
    carry **no date convention whatsoever**, so as-of dates must be parsed from each entry's
    anchor *text* ("December 2023"), never from the URL — the exact inverse of HDFC, where the
    URL is the only reliable source. Its combined workbook also has no Index sheet and puts the
    constant literal "quant Mutual Fund" in row 1 with the real scheme name in **row 2**,
    unique so far among combined-workbook AMCs: neither shared resolver fits
    (`find_sheet_code` needs an Index sheet, `find_sheet_by_title` reads row 1), so the adapter
    carries its own small row-2 variant rather than bending the shared helper for one AMC.
  - **Navi** is a WordPress/Elementor page whose REST endpoint
    (`POST /wp-json/nv/v1/documents`, `category=884` for Monthly Portfolio) is spelled out in
    the theme's own static `app.js` — again no browser needed. Three quirks: it requires a
    `WP-NONCE` header that is genuinely enforced (omitting it 403s) but is WordPress's standard
    *anonymous* nonce — identical for every visitor and openly embedded in the page's inline
    `navi_property` variable, so the adapter scrapes it once per discovery call and reuses it;
    the endpoint has no bulk-list mode (`financial_year` and full month name are both
    mandatory), so history means one POST per calendar month; and files are served from two
    different hosts by era (`public-assets.prod.navi-tech.in` recent,
    `public-navi-docs.s3.ap-south-1.amazonaws.com` older) where a 2022-2024 range of URLs carry
    **no file extension at all** despite serving correct XLSX content-types. The adapter
    therefore never filters on extension, deferring to the shared content sniff — a filter that
    looked obviously safe for every other AMC would have silently dropped three years of Navi
    files. Pre-2021 months publish one combined legacy `.xls` per AMC rather than per-scheme
    workbooks; those are surfaced and then skipped by the existing `skipped_format` path rather
    than dropped at discovery, so the gap is visible instead of invisible.
  - **Zerodha** is a Next.js page but server-side-rendered, so the entire archive — 360
    monthly files, Nov 2023 to Aug 2026 — arrives embedded as JSON in `__NEXT_DATA__` on one
    plain GET; "history" needs no pagination or query params at all, just filtering what is
    already in hand. The same blob carries the scheme-code map (`ZNFTY` = "Zerodha Nifty 50
    Index Fund"), which the adapter needs because filenames use short codes, not names. Files
    live on a separate `assets.zerodhafundhouse.com` host, fetchable with the honest UA. Its
    filenames are hand-inconsistent in three separate ways — dash spacing (`ZNFTY - Monthly`
    vs `ZEN50- Monthly`), month spelling (`August 2026`, `Aug 2025`, `Sept 2025`) and double
    spaces — so matching is deliberately loose. The two oldest files (Nov/Dec 2023) predate
    Zerodha's per-scheme split and are combined workbooks with no scheme-code prefix; the
    adapter returns them only when no `scheme_hint` filter is given.
  - **Axis is live, and its blocked entry was measuring the wrong thing.** It was judged on a
    browser-automation route that turned out to be unnecessary: the document listing is a plain
    JSON API on `www.axismf.com` itself, callable with `httpx` and the honest User-Agent, so
    the Playwright fingerprinting that stopped the previous attempt never had to be involved.
    `POST /cms/token` with `{}` yields a bearer token with no login; `POST
    /cms/get-scheme-documents` with `{"sdType":"yearMonthSchemeDocs","sdID":"sdMonthSchemePortfolio"}`
    returns scheme categories, years and months, and re-posting with `year`/`month`/`schemeCode`
    returns a same-origin `.xlsx` URL under the key `docuementURL` (the AMC's own typo, matched
    verbatim). **The generalizable find:** Axis's own bundled JS sets
    `API_ENCRYPTION_STATUS_CMS: "none"` for `/cms/*` while its transactional API is set to
    `"enable"` — i.e. an AMC can run a plaintext CMS tier *alongside* an encrypted one. Worth
    checking such a flag in bundled JS before concluding an AMC's payloads are encrypted, which
    is precisely the verdict currently standing against Bandhan, JM Financial and Mahindra
    Manulife.
  - **Kotak is the cleanest proof of the portal/file-host split**, because unlike HDFC its
    recorded verdict was *accurate*: `www.kotakmf.com`, homepage included, really is
    whole-domain Radware Bot Manager — every request 302s to `validate.perfdrive.com` with
    `server: rdwr` and a `stormcaster.js` challenge. Accurate, and irrelevant.
    `vatseelabs-s3.kotakmf.com` is a separate unblocked CloudFront/S3 host serving every
    investor document, found by a public search for a real Kotak filename. The URL needs
    neither a scheme name nor a listing — it is computable from the as-of date alone. **Real
    capability limit:** that bucket keeps only a rolling ~4-month window (May-Aug 2026 return
    200; April 2026 and earlier genuinely 403, probed systematically back to Jan 2024) —
    absence, not a second naming scheme. Unlike HDFC's bucket, `HEAD` works here. Kotak's
    per-scheme sheets use a *merged* "Name of Instrument" header spanning three columns, so row
    data sits two columns right of where header detection expects it; left alone the parser
    reads the ISIN as the instrument name and drops the real ISIN. That is repaired in the
    adapter's own `fetch()`, which returns a single repaired sheet — so it needs no
    `sheet_resolver` despite Kotak publishing a combined workbook.
  - **ICICI Prudential** was judged on an F5 `TSbd` anti-bot script that is real but only ever
    guards the SPA shell — which loads fine for the honest User-Agent and never challenges it.
    Two further traps sat on top: every non-root path returns a **200 SPA shell carrying a 404
    status** (cosmetic, resolved client-side by a real browser), and the listing page's own
    `apimf.icicipruamc.com` API 401s cross-origin — a genuine dead end that looks like the
    answer. The actual Download button ignores that API entirely and opens a static Azure Blob
    URL under `www.icicipruamc.com/blob/downloads/...`, public and plain-`200` to the honest UA.
    The path is computable, with one manual-upload artifact: the month folder is three-letter
    except May/June/July, which are spelled out. Unlike every other AMC so far, each month is
    **one ZIP containing every scheme's xlsx**, so `fetch()` downloads and extracts the member
    matching the scheme. Coverage starts January 2025, matching the site's own displayed
    history. This is the only AMC to date that required a *shared*-parser change (see below).
  - **Quantum** (quantumamc.com — not to be confused with *quant* Mutual Fund) is plain
    server-rendered HTML with no SPA, no JS literal and no JSON endpoint. The cheapest source of
    each file URL turned out to be the analytics call attached to every download link —
    `onclick="GTMcodeforxml(url, page, title, subtitle)"` — which carries both the file URL and
    a human-readable title, scraped with one regex. File URLs are opaque UUIDs under
    `/FileCDN/FactSheet/<uuid>.xlsx` with **no date embedded**, so the as-of date is recoverable
    only from the anchor's own title text ("August 2026 - All Funds") — the same
    date-from-text-not-URL situation as quant. **History lever:** the registered URL's numeric
    path segments are a real server-side filter,
    `/portfolio/combined/{scheme_id}/{portfolio_type}/{year}/{month}` (scheme_id `-1` = all
    funds, portfolio_type `1` = monthly). AMFI registers the `year=0,month=0` default, which
    returns only the last ~20 months; requesting an explicit `{year}` with `month=0` returns
    every month published that year, verified 2015-2026 (2011/2012 return nothing — coverage
    starts mid-2015). Had the default URL been taken at face value, this adapter would have
    shipped with a two-year window instead of a decade. Its Index sheet reads "Scheme Full
    Name", which the shared resolver's `"scheme name"` substring test misses because "full"
    breaks contiguity; handled with a local resolver rather than loosening the shared helper.
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
