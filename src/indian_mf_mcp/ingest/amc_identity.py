"""Single source of truth for AMC identity.

Three independent AMC naming schemes exist in this codebase and none of them joined
before this module:

  1. **Adapter key** — what `mf-mcp --amc` takes and what `registry.ADAPTERS` is keyed by:
     ``"ppfas"``, ``"bank-of-india"``, ``"360-one"``.
  2. **Adapter class attribute** — ``AMCAdapter.amc_id``: ``"amc-ppfas"``, ``"amc-nippon-india"``.
  3. **Database `scheme.amc_id`** — derived by slugging AMFI's own AMC name out of
     NAVAll.txt: ``"amc-ppfas-mutual-fund"``, ``"amc-bank-of-india-mutual-fund"``.

Because (1)/(2) never mapped onto (3), three separate features were silently inert:
`mf-mcp backfill --amc <amc>` always found zero schemes, adapter-health staleness
warnings in get_fund_portfolio could never match a row, and the scheme→adapter-hint
auto-registration had nothing to key on. The fix is one explicit table, here, rather
than fuzzy name matching at each call site — AMC names are a closed set of ~54 that
changes a couple of times a year, and a wrong fuzzy match would attribute one AMC's
holdings to another.

`AMFI_AMC_NAME` maps adapter key → AMFI's canonical AMC name exactly as it appears in
the NAVAll.txt header line. `amc_ids_for_adapter()` resolves that against the `amc`
table actually present in the store, so a historical AMFI renaming (AMFI published full
legal names such as "Parag Parikh Financial Advisory Services Private Limited" before
switching to short "PPFAS Mutual Fund" form) still resolves via the alias list rather
than dropping the AMC's whole back catalogue on the floor.
"""
from __future__ import annotations

import re
import sqlite3

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _NON_ALNUM_RE.sub("-", text.lower()).strip("-")


def amc_id_for(amc_name: str) -> str:
    """DB primary key for an AMC, derived from AMFI's own name for it."""
    return f"amc-{slug(amc_name)}"


# Adapter key -> AMFI's canonical AMC name (as printed in NAVAll.txt).
# Verified against a live NAVAll.txt fetch; keep in sync when adding an adapter.
AMFI_AMC_NAME: dict[str, str] = {
    "360-one": "360 ONE Mutual Fund",
    "axis": "Axis Mutual Fund",
    "bajaj-finserv": "Bajaj Finserv Mutual Fund",
    "bandhan": "Bandhan Mutual Fund",
    "bank-of-india": "Bank of India Mutual Fund",
    "baroda-bnp-paribas": "Baroda BNP Paribas Mutual Fund",
    "dsp": "DSP Mutual Fund",
    "franklin-templeton": "Franklin Templeton Mutual Fund",
    "groww": "Groww Mutual Fund",
    "hdfc": "HDFC Mutual Fund",
    "hsbc": "HSBC Mutual Fund",
    "icici-prudential": "ICICI Prudential Mutual Fund",
    "invesco": "Invesco Mutual Fund",
    "iti": "ITI Mutual Fund",
    "kotak": "Kotak Mahindra Mutual Fund",
    "lic": "LIC Mutual Fund",
    "mirae": "Mirae Asset Mutual Fund",
    "motilal-oswal": "Motilal Oswal Mutual Fund",
    "navi": "Navi Mutual Fund",
    "nippon": "Nippon India Mutual Fund",
    "ppfas": "PPFAS Mutual Fund",
    "quant": "quant Mutual Fund",
    "quantum": "Quantum Mutual Fund",
    "sbi": "SBI Mutual Fund",
    "sundaram": "Sundaram Mutual Fund",
    "tata": "Tata Mutual Fund",
    "taurus": "Taurus Mutual Fund",
    "trust": "Trust Mutual Fund",
    "union": "Union Mutual Fund",
    "uti": "UTI Mutual Fund",
    "zerodha": "Zerodha Mutual Fund",
}

# Historical AMFI spellings that resolve to the same adapter. AMFI has renamed AMCs in
# NAVAll.txt over the archive window this project backfills, and scheme rows ingested
# under an old name keep the old amc_id forever (they are never rewritten — see
# repository.upsert_scheme). Listing them keeps a backfill from missing that history.
AMFI_AMC_ALIASES: dict[str, tuple[str, ...]] = {
    "ppfas": ("Parag Parikh Financial Advisory Services Private Limited",),
    "bandhan": ("IDFC Mutual Fund",),
    "hsbc": ("L&T Mutual Fund",),
    "360-one": ("IIFL Mutual Fund",),
    "groww": ("Indiabulls Mutual Fund",),
    "navi": ("Essel Mutual Fund",),
    "bank-of-india": ("BOI AXA Mutual Fund",),
}


def amc_ids_for_adapter(adapter_key: str) -> list[str]:
    """Every DB amc_id this adapter's schemes could be filed under, current name first."""
    names = [AMFI_AMC_NAME[adapter_key], *AMFI_AMC_ALIASES.get(adapter_key, ())]
    seen: list[str] = []
    for name in names:
        candidate = amc_id_for(name)
        if candidate not in seen:
            seen.append(candidate)
    return seen


def _reverse_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for key in AMFI_AMC_NAME:
        for amc_id in amc_ids_for_adapter(key):
            index[amc_id] = key
    return index


_BY_AMC_ID = _reverse_index()


def adapter_key_for_amc_id(amc_id: str | None) -> str | None:
    """Reverse lookup: DB amc_id -> adapter key, or None if no adapter covers this AMC.

    None is the normal answer for the ~23 AMCs with no portfolio adapter yet; callers
    treat it as "no coverage", never as an error.
    """
    if not amc_id:
        return None
    return _BY_AMC_ID.get(amc_id)


# Adapter key -> the `amc_id` attribute that adapter class declares. Distinct from the
# AMFI-derived ids above (an adapter says "amc-ppfas"; the store says
# "amc-ppfas-mutual-fund"), and mostly but NOT always "amc-" + adapter key — nippon
# declares "amc-nippon-india". `adapter_health` rows are keyed by this namespace, so a
# reader holding a DB amc_id needs this hop to find them. Duplicated here rather than
# imported from registry so the MCP read path does not pull in openpyxl and all 31
# adapter modules; test_amc_identity.py asserts the two stay in sync.
ADAPTER_DECLARED_AMC_ID: dict[str, str] = {
    "360-one": "amc-360-one",
    "axis": "amc-axis",
    "bajaj-finserv": "amc-bajaj-finserv",
    "bandhan": "amc-bandhan",
    "bank-of-india": "amc-bank-of-india",
    "baroda-bnp-paribas": "amc-baroda-bnp-paribas",
    "dsp": "amc-dsp",
    "franklin-templeton": "amc-franklin-templeton",
    "groww": "amc-groww",
    "hdfc": "amc-hdfc",
    "hsbc": "amc-hsbc",
    "icici-prudential": "amc-icici-prudential",
    "invesco": "amc-invesco",
    "iti": "amc-iti",
    "kotak": "amc-kotak",
    "lic": "amc-lic",
    "mirae": "amc-mirae",
    "motilal-oswal": "amc-motilal-oswal",
    "navi": "amc-navi",
    "nippon": "amc-nippon-india",
    "ppfas": "amc-ppfas",
    "quant": "amc-quant",
    "quantum": "amc-quantum",
    "sbi": "amc-sbi",
    "sundaram": "amc-sundaram",
    "tata": "amc-tata",
    "taurus": "amc-taurus",
    "trust": "amc-trust",
    "union": "amc-union",
    "uti": "amc-uti",
    "zerodha": "amc-zerodha",
}


def health_lookup_ids(amc_id: str | None) -> list[str]:
    """Every id an `adapter_health` row for this store amc_id could have been written under.

    Returns [] when no adapter covers the AMC, so the caller skips the query entirely.
    """
    key = adapter_key_for_amc_id(amc_id)
    if key is None:
        return []
    return [ADAPTER_DECLARED_AMC_ID[key], key]


def present_amc_ids_for_adapter(conn: sqlite3.Connection, adapter_key: str) -> list[str]:
    """Subset of amc_ids_for_adapter() that actually exists in this store's `amc` table.

    Returns [] when the AMC has not been seen in any ingested NAVAll.txt — which is the
    signal that `mf-mcp ingest-navall` has not been run, not that the AMC is unknown.
    """
    candidates = amc_ids_for_adapter(adapter_key)
    placeholders = ",".join("?" * len(candidates))
    rows = conn.execute(
        f"SELECT amc_id FROM amc WHERE amc_id IN ({placeholders})", candidates
    ).fetchall()
    found = {r["amc_id"] for r in rows}
    return [c for c in candidates if c in found]
