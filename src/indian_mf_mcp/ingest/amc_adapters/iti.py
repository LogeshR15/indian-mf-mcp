"""ITI Mutual Fund adapter.

Ground-truthed live 2026-09-13. AMFI's own registry names ITI's disclosure page as
https://www.itiamc.com/statuory-disclosure (the "statuory" typo is ITI's own path, not a
transcription error here). That URL is an Angular SPA shell — the raw HTML response contains
no document links at all (confirmed: a plain `httpx.get()` on that path returns ~275KB of
Angular boilerplate with no filenames anywhere in it, no `__NEXT_DATA__`/TransferState
equivalent either). Everything is populated client-side after bootstrap by one POST call.

Discovery, step by step:
  1. The Angular app's lazy `main.<hash>.js` bundle (fetched once, searched for literal
     patterns, no Playwright needed) contains an `ApiService` class listing every backing
     endpoint as `static <name>=c.baseApiUrl+"<path>"`, where `baseApiUrl` resolves to
     `https://itiamc.com/jeeth/api/v1/catalog/`. The "Portfolio Disclosures" tab on the
     statutory-disclosure page calls `k.f.getPartnerFiles`, i.e.
     `POST https://itiamc.com/jeeth/api/v1/catalog/getPartnerDocumentByType`
     with a plain JSON body `{"type": "Disclosure"}` (found by reading the compiled
     `getFiles=()=>{...this.apiService.postData(k.f.getPartnerFiles,{type:"Disclosure"})...}`
     component method directly out of the bundle text).
  2. **The request/response bodies are AES-encrypted** — this is exactly the "encrypted API
     tier" trap called out in CONTRIBUTING.md: `environment.ts` ships a hardcoded flag
     `isEncryptionEnabled:true`, and the same bundle's `EncryptionProviderService` carries the
     key/IV in cleartext:
         key = Latin1("aar6tzij8o1snaar")   # 16 bytes -> AES-128
         iv  = Latin1("0123456789ABCDEF")   # 16 bytes
         AES-CBC, PKCS7 padding
     `ApiService.postData` wraps the real payload as
     `{...body, guid: <32 random alnum chars>, timeStamp: Date.now()}`, JSON-stringifies it,
     AES-CBC/PKCS7-encrypts it, base64s the ciphertext, and POSTs `{"eData": "<base64>"}`.
     The response is `{"eData": "<base64>"}` too, decrypted the same way to recover
     `{"status":0,"data":{...}}`. None of this is an access control — the key ships in the
     client's own JS so any browser hitting this page performs exactly this exchange; it is
     reproduced here with plain `httpx` + the already-available `cryptography` package (no new
     dependency added), the same way LIC's and Sundaram's adapters replay a discovered request
     shape. `guid` and `timeStamp` are per-request nonces the server does not appear to
     validate strictly (any freshly generated 32-char alnum string plus current epoch-ms
     works); they are generated fresh per call here regardless.
  3. The decrypted response is one big JSON blob covering *every* statutory-disclosure
     category (`data.typeList[]`, each with a `subType`); the one we want is
     `subType == "Portfolio Disclosures"`, whose `subTypesList[]` breaks out by `topic` into
     "Half yearly", "Monthly", and "Fortnightly". Each topic's `topicsList[]` entry is
     `{id, url, fileName, month, year}` — `url` is a directly-fetchable, already-public file
     URL under `https://itiamc.com/admin/pdf/...` (same host, no separate CDN). `fileName` is
     the human title (e.g. "Monthly Portfolio - August 2026"); `month`/`year` on the item are
     the *publication* month (one month after the as-of month — e.g. the "December 2025"
     filing carries month="January", year="2026"), so the as-of date is parsed from the
     filename's own embedded month/year instead, mirroring Union's approach of trusting the
     more literal signal over a metadata field that means something subtly different.

  How history is reached (constraint #4): **one single API call returns the full archive** —
  no pagination, no per-year parameter, no year/month filter needed. As ground-truthed
  2026-09-13, the "Monthly" topic alone carried 89 entries running back to April 2019. Month
  names in `fileName` are inconsistent across eras: "Monthly Portfolio - August 2026" (spaced
  dash, full month name) for anything since ~2023, "Monthly Portfolio -December 2024" /
  "Monthly Portfolio-August 2024" (missing space before/after the dash) through 2024, and
  short forms "Monthly Portfolio - Feb 2023" / "Monthly Portfolio - Apr 2019" going further
  back — all handled by one regex that tolerates optional dash spacing and 3-letter-or-full
  month names.

  File shape: one combined workbook per month covering every live scheme (an "Index" sheet
  with "Short Name" / "Scheme Name" columns — already a recognised keyword pair in the shared
  `combined_workbook.find_sheet_code`, so this needs **no shared-parser change** — plus one
  sheet per scheme, e.g. "ITIFCF" for ITI Flexi Cap Fund). Column header on each scheme sheet
  is "% to Net\\n Assets" (embedded newline; the shared header-detector's `"% to net"`
  substring test matches across it with no change needed) and the grand-total row is a plain
  "GRAND TOTAL" summing to exactly 1.0 — no quirks hit in `xlsx_portfolio.py` at all.
  Register with `sheet_resolver=combined_workbook.find_sheet_code` (Tata/Sundaram-style),
  not `None`.

  Pre-2023 files are legacy `.xls` (BIFF) rather than `.xlsx`; `ingest_scheme_portfolios`
  already skips those via `skipped_format` (the project has no BIFF parser yet) — a real, not
  fabricated, coverage gap for the oldest history, not something this adapter hides.

  Nothing on this host resembles the "portal blocked, files open" split seen elsewhere: both
  the SPA shell and every `/admin/pdf/...` file URL answer this project's honest
  `config.USER_AGENT` with a clean `200` and no anti-bot challenge.
"""
from __future__ import annotations

import base64
import json
import random
import re
import string
import time
from datetime import date, datetime

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from indian_mf_mcp import config
from indian_mf_mcp.ingest.amc_adapters.base import DocType, DocumentRef

API_URL = "https://itiamc.com/jeeth/api/v1/catalog/getPartnerDocumentByType"

# Shipped in cleartext in ITI's own Angular bundle (EncryptionProviderService); not a secret
# this project extracted by defeating anything — any browser loading the page uses the same
# key to talk to the same endpoint.
_AES_KEY = b"aar6tzij8o1snaar"
_AES_IV = b"0123456789ABCDEF"

_FILENAME_MONTH_RE = re.compile(
    r"Monthly Portfolio\s*-?\s*([A-Za-z]+)\s+(\d{4})", re.IGNORECASE
)


def _encrypt(plaintext: str) -> str:
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV)).encryptor()
    ct = encryptor.update(data) + encryptor.finalize()
    return base64.b64encode(ct).decode("ascii")


def _decrypt(b64_ciphertext: str) -> str:
    ct = base64.b64decode(b64_ciphertext)
    decryptor = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV)).decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


def _guid() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(32))


def _parse_as_of(file_name: str) -> date | None:
    m = _FILENAME_MONTH_RE.search(file_name)
    if not m:
        return None
    month_name, year_str = m.groups()
    for fmt in ("%B", "%b"):
        try:
            month_num = datetime.strptime(month_name, fmt).month
            break
        except ValueError:
            continue
    else:
        return None
    import calendar
    year = int(year_str)
    last_day = calendar.monthrange(year, month_num)[1]
    return date(year, month_num, last_day)


class ITIAdapter:
    amc_id = "amc-iti"

    def _call_api(self, body: dict, client: httpx.Client | None = None) -> dict:
        payload = dict(body)
        payload["guid"] = _guid()
        payload["timeStamp"] = int(time.time() * 1000)
        eData = _encrypt(json.dumps(payload))
        headers = {"User-Agent": config.USER_AGENT, "Content-Type": "application/json"}
        post = client.post if client is not None else httpx.post
        resp = post(API_URL, json={"eData": eData}, headers=headers, timeout=30)
        resp.raise_for_status()
        wrapped = resp.json()
        if "eData" not in wrapped:
            return wrapped
        return json.loads(_decrypt(wrapped["eData"]))

    def list_documents(self, doc_type: DocType, since: date,
                        scheme_hint: str | None = None,
                        client: httpx.Client | None = None) -> list[DocumentRef]:
        if doc_type != DocType.MONTHLY_PORTFOLIO:
            return []

        data = self._call_api({"type": "Disclosure"}, client=client)
        if data.get("status") != 0:
            return []

        type_list = data.get("data", {}).get("typeList", [])
        portfolio_section = next(
            (t for t in type_list if t.get("subType") == "Portfolio Disclosures"), None
        )
        if portfolio_section is None:
            return []

        monthly_topic = next(
            (st for st in portfolio_section.get("subTypesList", []) if st.get("topic") == "Monthly"),
            None,
        )
        if monthly_topic is None:
            return []

        refs: list[DocumentRef] = []
        seen = set()
        for item in monthly_topic.get("topicsList", []):
            file_name = item.get("fileName", "")
            url = item.get("url")
            if not url:
                continue
            as_of = _parse_as_of(file_name)
            if as_of is None or as_of < since:
                continue
            key = (url.split("?")[0], as_of)
            if key in seen:
                continue
            seen.add(key)
            refs.append(DocumentRef(url=url, doc_type=DocType.MONTHLY_PORTFOLIO,
                                     as_of_date=as_of, scheme_hint=scheme_hint))
        refs.sort(key=lambda r: r.as_of_date)
        return refs

    def fetch(self, ref: DocumentRef, client: httpx.Client | None = None) -> bytes:
        headers = {"User-Agent": config.USER_AGENT}
        get = client.get if client is not None else httpx.get
        resp = get(ref.url, headers=headers, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        return resp.content
