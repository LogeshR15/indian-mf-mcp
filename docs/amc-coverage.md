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
  - **AMC coverage today (29): 360 ONE, Axis, Bajaj Finserv, Bank of India, Baroda BNP Paribas,
    DSP, Franklin Templeton, Groww, HDFC, HSBC, ICICI Prudential, ITI, Kotak Mahindra, LIC,
    Mirae Asset, Motilal Oswal, Navi, Nippon India, PPFAS, quant, Quantum, SBI, Sundaram, Tata,
    Taurus, Trust, Union, UTI, and Zerodha** (see
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
  - **Seven AMCs remain blocked, down from fifteen — and eight of those fifteen were
    misdiagnosed, not blocked.** HDFC, Kotak, Axis, ICICI Prudential and quant were live as of
    2026-09-13, and Invesco, Bandhan and HSBC joined them live as of 2026-09-14 (details below).
    That is a 53% error rate in the original triage, and the errors were not random: each came
    from testing the wrong surface, or simply not testing the right one. HDFC and Kotak were
    judged on their *investor portals* rather than their file hosts; Axis was judged on a
    *browser route* that was never required; quant was judged on the *wrong page*, one whose
    content is click-gated; ICICI was judged on an anti-bot script that only ever guarded the
    SPA shell; Invesco was judged on a WAF verdict that does not reproduce against the current
    site at all (possibly a stale verdict from before a site rebuild, or a mis-attribution from
    its sibling entry, WhiteOak Capital); Bandhan was judged on its encrypted transactional API
    without checking whether a separate plaintext tier existed alongside it; HSBC was judged on
    AMFI's own registered URL, which — unusually — turned out to be a real, working,
    server-rendered page that simply does not carry the monthly portfolio disclosure at all; the
    actual archive lives one click away, on a page AMFI never registers. **The portal is not the
    product, and neither is the page a regulator happens to link.** Every verdict below was
    reached the same way and none should be trusted until re-tested against the file host, any
    static JS asset the site already serves, AMFI's own registered URL, and that URL's own site
    navigation for a sibling page the registry doesn't know about.
  - **Still blocked, by failure mode:**
    - *Commercial WAF on the whole domain* — **WhiteOak Capital** (CloudFront/AWS-WAF).
      **Invesco** was removed from this entry 2026-09-14 — it's live now, see below.
      **Edelweiss** was re-tested live 2026-09-14, specifically looking for the HDFC/Kotak-style
      portal/file-host split — see its own entry below; the split does not hold here and the
      verdict stands as blocked, but with much better evidence now. Worth re-testing WhiteOak
      the same way, since it shared this entry with an AMC whose verdict didn't hold up.
    - *Headless-JS fingerprinting* — **PGIM India**: the real API is same-origin but reachable
      only after client-side JS the site fingerprints and blocks in Playwright. Worth
      re-testing the way Axis was solved — by reading its bundled JS for the request shape
      instead of driving a browser at all.
    - *Application-layer payload encryption* — **JM Financial** and **Mahindra Manulife** both
      return a real `200 OK` whose `{"data"/"payload": "<base64>"}` body decodes to
      high-entropy ciphertext rather than JSON. Replaying a captured request fails; decrypting
      would mean reverse-engineering each site's client-side crypto, which is out of scope.
      **Bandhan was originally grouped here too and turned out to be misdiagnosed** — see
      below. The repetition across JM Financial and Mahindra Manulife still suggests a shared
      fintech backend vendor for their transactional tiers, and per the Axis/Bandhan precedent
      both deserve a re-test for a separate plaintext CMS tier before the verdict stands.
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
  - **Invesco moved from "blocked (commercial WAF)" to live, and neither half of the old
    verdict held up on re-test 2026-09-14.** First, entity status: Invesco Asset Management
    (India) is not defunct or renamed. Religare Invesco (2013) became Invesco Asset Management
    after Invesco bought out Religare's stake (2015-16); in April 2024 Hinduja Group's IndusInd
    International Holdings acquired a 60% stake, becoming joint sponsor alongside the
    US-based Invesco (regulatory approvals completed by late 2025) — no renaming of the AMC or
    its schemes has happened, and every document on the live site is still branded "Invesco
    India ...". Second, the WAF itself: `www.invescomutualfund.com` returns a clean `200` to
    the honest User-Agent on the bare homepage and every path tried — no CloudFront/AWS-WAF
    challenge anywhere, contradicting the recorded verdict outright (most likely stale, from
    before the site's Next.js rebuild, or mis-attributed from its sibling entry, WhiteOak
    Capital, which shared the same line in this doc). What *is* stale is AMFI's own registered
    URL: `.../literature-and-form?tab=Statutory` 301-redirects to
    `/literature-forms/forms/application`, silently dropping the `tab` query string, because
    the old query-param route no longer exists on the rebuilt site — AMFI is simply pointing at
    a dead convention. Discovery needed one build-time browser network capture (never used at
    runtime) of the live site's own "Monthly Holdings" tab, which turned up a same-origin JSON
    listing API needing no scheme-name guessing at all:
    `GET /api/CompleteMonthlyHoldings?year=<YYYY>&classification=<equity|fixed-income|hybrid|
    fund-of-funds|exchange-traded-fund|index-funds|fixed-maturity-plans>`, one call per
    (year, classification) returning *every* scheme in that category for that year, each with
    twelve direct, already-versioned, already-fetchable `.xlsx` URLs (Jan-Dec) on
    `www.invescomutualfund.com`'s own CMS document-library path — no separate CDN/S3 host at
    all, unlike HDFC/Kotak. Verified back to 2012 (`year=0` lists every year with data,
    2012-2026); the legacy-`.xls`-to-real-`.xlsx` transition happens between 2020 and 2021.
    One file per scheme per month, zero shared-parser changes: the August 2026 Invesco India
    ELSS Tax Saver Fund file reconciles at exact 100%, 80 of 93 parsed rows carrying a real
    ISIN (the rest are harmless footer pseudo-rows the shared parser's stop-markers don't yet
    recognise for this AMC's footer wording — they carry no weight, so reconciliation is
    unaffected and the shared parser was left alone per this project's rule). Also worth
    noting: the "Monthly Holdings" category is a genuine, separate tab from "Fortnightly
    Holdings" and "Half Yearly Holdings" on the live site — the AMFI-registered URLs that only
    named the latter two undersold what the AMC actually publishes.
  - **Bandhan is live, and its blocked verdict was reached by testing only one of two API
    tiers.** It had been grouped with JM Financial and Mahindra Manulife under "application-
    layer payload encryption" because `pnservices.bandhanmutual.com/internal/investorservices/
    encdec` — the transactional tier, named literally "encdec" in the site's own bundled JS —
    really does return encrypted payloads. But `bandhanmutual.com`'s 11MB obfuscated
    `main.<hash>.js` also names a second host, `cmsnew.bandhanmutual.com`, called for its
    monthly-factsheets/FAQ content. That CMS's *default* WordPress REST namespace is genuinely
    locked down site-wide (`GET /wp-json/` 401s with a "DRA: Only authenticated users..."
    plugin message — a real, accurate block, same shape as Kotak's whole-domain WAF), but a
    *separate*, custom `finance-api/v1` namespace the same CMS registers has its own public
    permission callback: `GET /wp-json/finance-api/v1/posts/monthly-portfolio` with the honest
    UA and no auth returns a clean `200` and real JSON — the entire archive, one call, 8 posts
    spanning 2018 and 2020–2026 (2019 absent — a real gap). **The generalizable find, and a
    second data point alongside Axis's `API_ENCRYPTION_STATUS_CMS` flag:** a locked-down or
    encrypted *default* API on a host doesn't rule out a separate, differently-registered
    plaintext tier living right next to it — worth checking for a second REST namespace/route
    table, not just an encryption-status flag, before recording an AMC as blocked on payload
    encryption. This directly reopens the question for JM Financial and Mahindra Manulife.
    Two further quirks made this AMC harder than the discovery alone suggests. First, the CMS
    silently *falls back* to an unrelated "latest posts" list for any category slug it doesn't
    recognise rather than 404ing, which cost real trial and error before landing on the exact
    right taxonomy slug (`monthly-portfolio`). Second, and more consequentially: Bandhan
    publishes **two** combined workbooks a month — "Debt Fund Portfolio" and "Equity Hybrid
    Fund Portfolios" — and only the Debt Fund one is usable at all. The Equity Hybrid workbook
    carries a `Company / Industry / (%) NAV` top-holdings summary with **no ISIN and no
    per-security detail whatsoever**, at every era sampled (2018, 2022, 2023, 2025); the shared
    parser correctly refuses it (no ISIN column to detect) rather than fabricating security-
    level data from a summary. And the Debt Fund workbook itself has a hard format-change
    boundary: every file from December 2018 through December 2024 uses the same ISIN-less
    `Name / Rating / Total` issuer-level rating-bucket layout (also unparseable), switching to
    the full SEBI-standard ISIN-complete layout starting exactly with the January 2025 file
    (confirmed by a roughly 5x file-size jump the same month). **Real capability limit:**
    reconciling history through this adapter is January 2025 onward only, verified at exactly
    100% on both the January 2025 and August 2026 files. Neither combined workbook has an Index
    sheet, and the scheme-title cell's row/column shifts release to release, so the adapter
    carries a local resolver that scans each sheet's own first ~6 rows for the first string
    that isn't a short internal code or a known template label, rather than assuming a fixed
    position — the same generalized-scan approach Trust and Bajaj Finserv needed for their own
    non-fixed layouts. `document_name` free text is unreliable as a *category* label (one March
    2025 entry is titled "...Debt Fund..." while its own URL and content are the Equity Hybrid
    workbook — a genuine CMS data-entry error) but is still the only source for each file's
    day-of-month, since day is not safely assumed to be month-end (some historical entries are
    a few days short of it, e.g. "26 February 2021" — a last-business-day filing, not month-
    end) — extracted by searching for the number preceding whichever month token appears,
    verified against all 124 real entries in the live archive with zero fallback needed.
  - **HSBC was live all along; the recorded "no discoverable disclosure page" verdict came
    from stopping at AMFI's own registered URL instead of the site's own navigation.** AMFI's
    registry names `investor-resources` (plus `Doc=fund-factsheets`/`Doc=other-disclosures`
    query-string variants) for this AMC — and that page really is plain, unblocked,
    server-rendered HTML carrying its entire document archive inline (~4,000 PDFs + ~350 XLSX,
    one page load, no pagination). The catch: none of the registered `Doc=` values do anything
    (the page ignores its own query string server-side and always renders the same full,
    unfiltered list — confirmed zero `Doc=`/`module-17` references anywhere in the response),
    and more importantly **the SEBI-mandated monthly portfolio holdings XLSX simply is not on
    this page** — its "Portfolios" category is ad-hoc PDFs (weekly debt summaries, half-yearly
    statements), and its "Fund factsheets" category is the glossy PDF factsheet, not the
    regulatory holdings statement. The real archive sits one click away, on a sibling page
    linked from the site's own primary navigation but never surfaced in AMFI's registry:
    `investor-resources/information-library`, whose "Fund portfolios" accordion section is a
    plain HTML `<table>` — one `<a href title>` per (scheme, month), ~2,200 links, the entire
    archive in one page load, the same "whole archive server-rendered inline" shape as
    Zerodha/Trust/360 ONE just via a plain table instead of a JS data blob. Files are Sitecore
    media-library paths on the AMC's own host (no separate CDN), rendered inconsistently with
    or without a leading slash and with inconsistent casing — both resolve identically once
    normalised. As-of dates come from each link's own `title` text (`"as on 31 August 2023"`,
    or no "as on" at all in newer rows), never from the URL: the folder-ID-shaped path segment
    in front of recent filenames (`document-DDMMYYYY/`) is an upload-batch ID, not the as-of
    date — ground-truthed by a folder stamped `document-07032023` containing a file titled
    "... as on 31 August 2023". **Real capability limit, not a bug:** HSBC renamed several
    schemes along the way ("Flexi Cap Equity Fund" → "Flexi Cap Fund" among others), and
    `scheme_hint` matching only reaches the current name — for HSBC Flexi Cap Fund that means
    46 months, November 2022 (the rename month) through August 2026, even though the archive's
    older, differently-named files for the same scheme reach back to October 2021. Three
    genuinely general parser gaps surfaced and were fixed in `xlsx_portfolio.py` rather than
    patched locally, since none of them are HSBC-specific hacks: HSBC spells its %-column out
    in full ("Percentage to Net Assets") instead of using "%", a wording variant added
    alongside the existing needles; its grand-total row reads "Total Net Assets as on
    31-August-2026" (a date-suffixed variant of the exact-match "total net assets"/"net assets"
    labels Tata needed), now matched as a prefix instead of an exact string; and its
    SEBI-mandated "Scheme Riskometer" footer block repeats the scheme's own name as a row label
    (with "Scheme Riskometer" only in the *industry* column), which defeats every
    prefix-based `FOOTER_STOP_MARKERS` check and previously leaked into holdings as a spurious
    entry — caught instead on the industry column's own fixed "riskometer" text; debt-scheme
    files also print two loose quant-indicator lines ("Annualised Portfolio YTM !", "Macaulay
    Duration") with no section header above them, added to the same marker list directly.
    Verified live across three files — HSBC Flexi Cap Fund August 2026 (85 holdings) and
    January 2022 (52 holdings, percentage-point scale correctly normalised), and HSBC
    Corporate Bond Fund August 2026 (83 holdings) — all reconciling to exact 100%, with the
    full test suite re-run clean after each parser change to confirm no other AMC regressed.
  - The remaining ~20 AMCs haven't been attempted yet. This is real, per-AMC engineering
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
