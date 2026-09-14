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
      making it moot). **Edelweiss** was re-tested live 2026-09-14, specifically looking for
      the HDFC/Kotak-style portal/file-host split — see its own entry below; the split does
      not hold here and the verdict stands as blocked, but with much better evidence now.
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
    - *AMC's own infrastructure is dead, not a control* — **Aditya Birla Sun Life**. This entry
      previously read "sandbox artifact, not a real blocker" — that was itself wrong, and it's
      worth recording why, because it's an object lesson in verifying rather than trusting a
      one-line prior note. Re-tested live 2026-09-14: it is not the sandbox, and it is not a
      control at all — ABSL's download infrastructure is genuinely dead, for everyone, and has
      been for a year and a half. AMFI's registered listing page,
      `mutualfund.adityabirlacapital.com/forms-and-downloads/portfolio`, loads cleanly (`200`,
      honest UA, no WAF, no SPA) and is plain server-rendered HTML whose "Monthly Portfolio"
      accordion tab carries its own backing call right in the markup:
      `data-accordian-api="/postlogin/CustomApi/Resources/FactsheetAccordionById?id=<guid>&ctype=<encoded-sitecore-path>"`.
      The site's own `global.js` (`resourceAccordianAjax()`) appends `&month= &year=0` before
      calling it — that literal space before the `&` is not a typo introduced here, it's copied
      verbatim from the site's own code, and the call `500`s without it. No auth, no encryption,
      no browser: one `GET` returns the AMC's entire monthly-portfolio archive — 211 entries,
      March 2009 through August 2026 — as clean JSON, `{ResourceLink, pdfUrl}` pairs with a
      human-readable as-of date already sitting in `ResourceLink` text (e.g. "Monthly Portfolios
      as on August 31, 2026"), no pagination needed. The same shape, same completeness, same
      one-call archive applies to Half Yearly (42 entries, its own guid) and Fortnightly (142
      entries) categories, and to unrelated resource types tried for comparison (e.g. "Monthly
      Scheme Performance"). Every `pdfUrl` returned, across every category tried with no
      exception, points at `https://abcscprod.azureedge.net/...` — and that hostname is
      `NXDOMAIN`. Confirmed authoritatively, not just against this sandbox's resolver: `dig`
      against it returns `status: NXDOMAIN` with the `SOA` in the authority section naming
      `ns1-06.azure-dns.com` — i.e. Azure's own DNS infrastructure is the one asserting the name
      doesn't exist, for `A`, `AAAA` and `CNAME` alike. This lines up exactly with Microsoft's
      published retirement of "Azure CDN from Edgio" (the classic `azureedge.net` product),
      retired 2025-01-15; ABSL built its resource library on that CDN and evidently never
      migrated off it, so every download link on its live production site — including the one
      its own `/shareresource` "preview/share" page serves for the same file — has been quietly
      404-by-DNS for well over a year. (For comparison, this project's other CDN-hosted AMCs —
      `files.hdfcfund.com`, `vatseelabs-s3.kotakmf.com` — both resolve and serve fine through
      this same sandbox and proxy, which is what rules out a local network-policy explanation
      here.) This is categorically different from every other entry on this page: there is no
      access control to evade, honestly or otherwise — there is simply no server answering the
      only URL the AMC itself publishes. No adapter is shipped, because there is no fetchable
      file to golden-test a parser against. If ABSL ever migrates its CDN, revisit from the
      accordion API documented above, which needs no rediscovery — only the dead hostname would
      need replacing.
    - **Edelweiss confirmed still blocked, re-tested 2026-09-14 — and it is a harder case
      than HDFC or Kotak, not a repeat of either.** Both of those turned out to be portal-only
      blocks with an unblocked file host one hop away; Edelweiss was re-tested specifically
      looking for that split and it does not exist. `www.edelweissmf.com` sits behind an
      Akamai edge WAF that guards the *entire* domain, not just `/statutory` or a backing API:
      the bare homepage (`GET https://www.edelweissmf.com/`) returns a flat `403` — Akamai's
      own "Access Denied" edge page, referencing `errors.edgesuite.net` with an `akamai-grn`
      trace id — to the honest User-Agent, identically to `/statutory`,
      `/statutory/portfolio-of-schemes` and `/downloads/factsheets`. Crucially this is not a
      shell-only block the way ICICI's or Trust's turned out to be: two real, currently-live
      document URLs were sourced independently of each other (one from a public
      `site:edelweissmf.com filetype:xlsx` search, one from AdvisorKhoj's third-party
      download index; both still point at `www.edelweissmf.com`, confirming Edelweiss has
      never moved its files off that domain) —
      `www.edelweissmf.com/Files/MF/Statutory/Portfolio_of_schemes/Monthly_Portfolio_and_RiskoMeter/EDEL_Portfolio_Monthly_Notes_31Jul2026_10082026130139.xlsx`
      (July 2026, the most recently published month at time of testing) and an equivalent June
      2026 URL — and both return the identical Akamai "Access Denied" signature as the
      homepage, not a 200. That rules out the HDFC/Kotak pattern directly: there is no
      separate `files.`/`cdn.`/S3 host to fall back to, because the real XLSX bytes are served
      from the same blocked domain as the page shell. Candidate separate hosts were checked
      and ruled out one by one: AMFI's own registered `online.edelweissmf.com` does not
      currently resolve at all (`NXDOMAIN` on a dangling CNAME to
      `online.edelweissmf.com.induscdn.com` — IndusCDN being a generic Indian RTA/transaction
      white-label vendor used by several AMCs), and by its name and CNAME target it reads as
      Edelweiss's investor-transaction portal rather than a document host even when live;
      `cdn1.edelweissfin.com` (the sister non-MF "Edelweiss Financial Services" brand, turned
      up by the same search) sits behind the identical Akamai edge and 403s the same way;
      guessed `files./static./assets./download.edelweissmf.com` subdomains simply don't
      resolve. No JS challenge, cookie, or redirect is involved anywhere in this — the
      response is a hard, edge-cached "Access Denied" (`cache-control: max-age=0`,
      `server-timing: cdn-cache; desc=HIT`, i.e. even the block itself is being served from
      cache), so there was no client-side bundle to read for a same-origin plaintext tier the
      way Axis's case resolved. Verdict stands, now on firmer evidence: genuinely blocked,
      domain-wide, no unblocked file host or subdomain found. Per spec.md's honest-UA policy
      and this project's rule against evading access controls, no further bypass was
      attempted.
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
  - **360 ONE** (formerly IIFL Asset Management, rebranded 2023 — visible in old filenames and
    even in OLE metadata authorship) is a Next.js App Router site using React Server Components
    streaming, *not* classic `__NEXT_DATA__`. The whole disclosures data model — every tab,
    category, year, month and file — arrives inline in the first HTML response as
    `<script>self.__next_f.push([1,"<chunk>"])</script>` tags; unescape a chunk's JSON string,
    strip its numeric id prefix, and it parses as valid JSON (React Flight references like
    `"$Lb"` are just plain strings). So the entire 2018-2026 archive, 104 monthly documents,
    comes from **one page load** with no AJAX, no pagination and no Playwright. The adapter
    searches the parsed tree recursively for the "Monthly Portfolio" subcategory rather than
    indexing into a fixed array position, so a re-ordered page does not silently break it.
    Files live on a separate public S3 bucket (`s3.ap-south-1.amazonaws.com/x-web-s3.360.one/`)
    that answers the honest UA cleanly. Filenames are a nine-year grab-bag with no usable
    convention, so discovery is required rather than computation — but discovery is cheap here.
    Two file-format notes: some recent files are *named* `.xls` while actually being ZIP-based
    xlsx (magic bytes confirm; the shared `sniff()` already handles it), and everything from
    2020 and earlier is genuine legacy BIFF `.xls`, correctly skipped as `skipped_format`.
    **Real capability limit: usable history through this pipeline starts 2021**, even though
    the listing advertises back to 2018.
  - **Groww** (formerly Indiabulls MF — the rebrand is still visible in its sheet codes, `IB01`,
    `IB02`, ...) is Next.js pages-router SSR: the entire statutory-disclosure archive, every
    category and financial year, is embedded in `__NEXT_DATA__` and comes back from one plain
    GET. History is a folder tree (`Portfolio` → one folder per Indian financial year,
    2022-2023 onward), so no per-month iteration. Files sit on a separate CDN host
    (`assets-netstorage.growwmf.in`), and each file's `publicUrl` is handed over verbatim and
    correctly percent-encoded, so nothing needs computing. **The notable quirk is that Groww's
    filenames contain real, repeated typos** — "Montlhy", "Fortnighlty", "Forthnightly",
    "Fotnightly" — so the adapter classifies monthly-vs-fortnightly by Levenshtein distance
    against the two reference words rather than by substring match, which also covers typos not
    yet observed. Date parsing is likewise format-tolerant, but deliberately only reads year and
    month: a monthly portfolio is a month-end by regulation, so the day is computed with
    `calendar.monthrange` rather than trusted from inconsistent filename text. Coverage starts
    March 2023 (its first post-rebrand disclosure); pre-2024 files are legacy `.xls` and skipped.
  - **Trust** is a client-side Vite/React SPA — the registered AMFI URL serves only
    `<div id="root">` plus a bundle, and the portfolio tab's content is click-gated, i.e.
    exactly the trap that produced the wrong "blocked" verdict on quant. No browser was needed
    anyway: the served bundle fetches runtime config from `/config.json`, which names a generic
    Cosmos-style API base, and every list on the site goes through one
    `POST Trust/GetData` query descriptor. `GetDisclosureByType` with slug
    `portfolio-monthly-disclosure` returns the **entire archive in one call** — 67 entries back
    to February 2021, no pagination. Filenames are hand-uploaded and inconsistent (`Monthly
    Port_<timestamp>.xlsx`, `Copy of Mont_....xlsx`, stray `-1`/`-002`/`_R` suffixes), so
    as-of dates come from each entry's `title` text ("... as on DD.MM.YYYY"), which is regular
    across the whole archive. Its combined workbook has no Index sheet and **flipped naming
    convention mid-archive** — terse all-caps acronyms with the real name in row 2 for older
    months, full scheme names from March 2026 — handled by a local resolver keying off
    `sheet_name.isupper()`, verified against both layouts. Two details for future maintainers:
    `fileurl` values point at the bare apex `trustmf.com` which 307-redirects to `www.`, so
    anything that bypasses `follow_redirects` will break; and only March-August 2026 are genuine
    `.xlsx` — most of the archive back to Feb 2021 is legacy `.xls` and is skipped downstream.
  - **Bajaj Finserv** is plain server-rendered WordPress (hello-elementor plus a custom
    "bajaj-downloads" plugin) with no static file links at all. A first-party static JS asset,
    `plugins/bajaj-downloads/assets/js/bajaj-downloads.js`, names three ordinary
    `admin-ajax.php` actions outright — `bajaj_get_filter_options` for years then months, and
    `bajaj_get_downloads` for a month's files. A WP AJAX nonce is required but ships verbatim in
    the page's own inline `var bajajDownloads = {...}`, needing no cookie, session or Referer.
    Files sit on a separate `media.bajajamc.com` host; neither host blocks the honest UA.
    History is a full archive walk (years → months → files), verified back to July 2023.
    **The dating trap here is the inverse of most:** the request parameter is a *fiscal* year
    ("2025-26"), so reconstructing dates from the request would misdate January, February and
    March by a whole year. The as-of date comes from each row's own title text instead
    ("...as on 31 Aug 2025"), tolerant of a missing "on", underscore-glued month/year, a
    misspelled "Septemeber", and titles carrying no day at all. Two more: some months serve
    `.xls` whose content is real OOXML (handled, since `sniff()` and openpyxl both work off
    magic bytes rather than the extension), and one sheet (`BFON`) has a **corrupted code cell**
    reading "hor" instead of its scheme code — so the adapter resolves sheets on column 1
    explicitly rather than reusing `find_sheet_by_title`, whose first-string-cell heuristic
    would happily pick up the corruption. A useful reminder that the shared heuristics are
    conveniences, not invariants.
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
