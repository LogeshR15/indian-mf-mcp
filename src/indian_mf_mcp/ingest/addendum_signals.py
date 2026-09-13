"""Extract change signals from addendum / notice PDF text (spec §4, §11, §14).

Addenda are the *legal event source* for:
  - Manager changes (appointment, cessation, change)
  - Benchmark changes
  - TER changes (material changes require addendum per SEBI)
  - Category / mandate revisions (scheme merger, re-categorisation)

Events extracted here get confidence='official' — the highest epistemic status —
because an addendum is a SEBI-mandated legal notice, not an observation from diffing
two factsheets.

Design:
  - Regex-based; no ML, no external API
  - Every signal carries raw_excerpt so Claude can read the source text
  - effective_date is extracted from the text if stated; falls back to doc_date
  - before/after fields are best-effort from the text; may be None
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ---------------------------------------------------------------------------
# Compiled patterns (SEBI-mandated language is fairly standardised)
# ---------------------------------------------------------------------------

# Manager change — matches: "Mr. XYZ has been appointed / ceases to be / has resigned"
_MANAGER_APPT_RE = re.compile(
    r"(?P<name>[A-Z][a-zA-Z \.]{3,40})\s+(?:has been |is hereby )?appointed?\s+"
    r"(?:as |the |an?)?(?:additional\s+)?fund\s+manager",
    re.IGNORECASE,
)
_MANAGER_CEASE_RE = re.compile(
    r"(?P<name>[A-Z][a-zA-Z \.]{3,40})\s+(?:has|will|shall)\s+"
    r"(?:ceased?\s+to\s+be|ceased?|resign(?:ed)?|step(?:ped)?\s+down|relinquish(?:ed)?)\s*"
    r"(?:to\s+be\s+|as\s+(?:the\s+)?|from\s+(?:the\s+)?role\s+of\s+|the\s+)?"
    r"(?:additional\s+)?fund\s+manager",
    re.IGNORECASE,
)
_MANAGER_CHANGE_GENERAL_RE = re.compile(
    r"change\s+(?:in|of)\s+(?:the\s+)?fund\s+manager",
    re.IGNORECASE,
)

# Effective date within the text: "w.e.f. 1st September 2026", "effective from 01-09-2026"
_EFFECTIVE_DATE_RE = re.compile(
    r"(?:w\.?e\.?f\.?|effective\s+(?:from|date)|with\s+effect\s+from)[:\s]*"
    r"(\d{1,2}[thstndrd]*\s+\w+\s+\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

# Benchmark change
_BENCHMARK_CHANGE_RE = re.compile(
    r"change\s+(?:in|of)\s+(?:the\s+)?(?:benchmark|tier[\s\-]+\d\s+benchmark)",
    re.IGNORECASE,
)
_BENCHMARK_FROM_RE = re.compile(
    r"existing\s+benchmark[:\s]+([^\n\.]{5,80})", re.IGNORECASE
)
_BENCHMARK_TO_RE = re.compile(
    r"(?:revised?|new|changed?\s+to)\s+benchmark[:\s]+([^\n\.]{5,80})", re.IGNORECASE
)

# TER change — "Total Expense Ratio (TER) ... X% ... Y%"
_TER_CHANGE_RE = re.compile(
    r"(?:total\s+expense\s+ratio|TER)\b.{0,60}?(?:revised?|changed?|increased?|decreased?|modified?)",
    re.IGNORECASE,
)
_TER_VALUE_RE = re.compile(
    r"(\d+\.\d+)\s*%\s*(?:per\s+annum|p\.?a\.?)?",
    re.IGNORECASE,
)

# Category / mandate change
_CATEGORY_CHANGE_RE = re.compile(
    r"(?:re-?categori[sz](?:ation|ed?)|merger?\s+of\s+scheme|change\s+(?:in|of)\s+(?:the\s+)?(?:scheme\s+)?(?:category|type|mandate|investment\s+objective))",
    re.IGNORECASE,
)

# Mandate / investment objective revision
_MANDATE_RE = re.compile(
    r"(?:change|revision|modification)\s+(?:in|of|to)\s+(?:the\s+)?(?:investment\s+objective|asset\s+allocation\s+pattern|investment\s+strategy)",
    re.IGNORECASE,
)

# Subscription / redemption halt
_HALT_RE = re.compile(
    r"(?:temporary|permanent)?\s*(?:suspension|closure|halt)\s+(?:of\s+)?(?:fresh\s+)?(?:subscription|purchase|redemption)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Date parsing helper
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_loose_date(text: str) -> Optional[str]:
    """Try to parse a date string like '1st September 2026' or '01-09-2026'. Returns ISO or None."""
    text = re.sub(r"(st|nd|rd|th)\b", "", text.strip(), flags=re.IGNORECASE).strip()
    # Try ISO
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # Try DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    # Try "DD MonthName YYYY"
    m = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if m:
        d, mon_str, y = int(m.group(1)), m.group(2).lower()[:3], int(m.group(3))
        mon = _MONTH_MAP.get(mon_str)
        if mon:
            try:
                return date(y, mon, d).isoformat()
            except ValueError:
                return None
    return None


def _extract_effective_date(text: str, doc_date: str) -> str:
    """Extract effective date from text, falling back to doc_date."""
    m = _EFFECTIVE_DATE_RE.search(text)
    if m:
        parsed = _parse_loose_date(m.group(1))
        if parsed:
            return parsed
    return doc_date


def _excerpt(text: str, match: re.Match, window: int = 200) -> str:
    """Return a window of text around a regex match for the raw_excerpt field."""
    start = max(0, match.start() - 50)
    end = min(len(text), match.end() + window)
    return text[start:end].replace("\n", " ").strip()


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

@dataclass
class ChangeSignal:
    event_type: str           # 'manager_change' | 'benchmark_change' | 'ter_change' |
                              # 'category_change' | 'mandate_revision' | 'addendum'
    effective_date: str       # ISO date
    before: Optional[str]     # Previous value, if extractable
    after: Optional[str]      # New value, if extractable
    confidence: str = "official"  # Always 'official' for addendum-sourced events
    raw_excerpt: str = ""
    sub_type: Optional[str] = None   # 'appointment' | 'cessation' for manager events


def extract_change_signals(pages: list[str], doc_date: str) -> list[ChangeSignal]:
    """Extract change signals from all pages of an addendum PDF.

    Args:
        pages: list of page text strings (from parsers.pdf_text)
        doc_date: ISO date of the addendum document (used as fallback effective_date)

    Returns:
        List of ChangeSignal, deduplicated by (event_type, effective_date, after).
    """
    full_text = "\n".join(pages)
    signals: list[ChangeSignal] = []
    seen: set[tuple] = set()

    def _add(signal: ChangeSignal) -> None:
        key = (signal.event_type, signal.effective_date, signal.after, signal.sub_type)
        if key not in seen:
            seen.add(key)
            signals.append(signal)

    # ---- Manager appointment ----
    for m in _MANAGER_APPT_RE.finditer(full_text):
        name = m.group("name").strip()
        eff = _extract_effective_date(full_text[max(0, m.start()-200):m.end()+200], doc_date)
        _add(ChangeSignal(
            event_type="manager_change",
            effective_date=eff,
            before=None,
            after=name,
            raw_excerpt=_excerpt(full_text, m),
            sub_type="appointment",
        ))

    # ---- Manager cessation ----
    for m in _MANAGER_CEASE_RE.finditer(full_text):
        name = m.group("name").strip()
        eff = _extract_effective_date(full_text[max(0, m.start()-200):m.end()+200], doc_date)
        _add(ChangeSignal(
            event_type="manager_change",
            effective_date=eff,
            before=name,
            after=None,
            raw_excerpt=_excerpt(full_text, m),
            sub_type="cessation",
        ))

    # ---- Manager change (general mention, no specific appointment/cessation) ----
    if not any(s.event_type == "manager_change" for s in signals):
        m = _MANAGER_CHANGE_GENERAL_RE.search(full_text)
        if m:
            eff = _extract_effective_date(full_text[max(0, m.start()-200):m.end()+200], doc_date)
            _add(ChangeSignal(
                event_type="manager_change",
                effective_date=eff,
                before=None,
                after=None,
                raw_excerpt=_excerpt(full_text, m),
                sub_type="change",
            ))

    # ---- Benchmark change ----
    m = _BENCHMARK_CHANGE_RE.search(full_text)
    if m:
        eff = _extract_effective_date(full_text[max(0, m.start()-200):m.end()+400], doc_date)
        before_m = _BENCHMARK_FROM_RE.search(full_text, m.start())
        after_m = _BENCHMARK_TO_RE.search(full_text, m.start())
        _add(ChangeSignal(
            event_type="benchmark_change",
            effective_date=eff,
            before=before_m.group(1).strip() if before_m else None,
            after=after_m.group(1).strip() if after_m else None,
            raw_excerpt=_excerpt(full_text, m, window=300),
        ))

    # ---- TER change ----
    m = _TER_CHANGE_RE.search(full_text)
    if m:
        eff = _extract_effective_date(full_text[max(0, m.start()-200):m.end()+300], doc_date)
        ter_values = _TER_VALUE_RE.findall(full_text[m.start():m.end()+300])
        before_ter = ter_values[0] if len(ter_values) >= 1 else None
        after_ter = ter_values[1] if len(ter_values) >= 2 else None
        _add(ChangeSignal(
            event_type="ter_change",
            effective_date=eff,
            before=f"{before_ter}%" if before_ter else None,
            after=f"{after_ter}%" if after_ter else None,
            raw_excerpt=_excerpt(full_text, m, window=300),
        ))

    # ---- Category / scheme type change ----
    m = _CATEGORY_CHANGE_RE.search(full_text)
    if m:
        eff = _extract_effective_date(full_text[max(0, m.start()-200):m.end()+300], doc_date)
        _add(ChangeSignal(
            event_type="category_change",
            effective_date=eff,
            before=None,
            after=None,
            raw_excerpt=_excerpt(full_text, m, window=300),
        ))

    # ---- Mandate / investment objective revision ----
    m = _MANDATE_RE.search(full_text)
    if m:
        eff = _extract_effective_date(full_text[max(0, m.start()-200):m.end()+300], doc_date)
        _add(ChangeSignal(
            event_type="mandate_revision",
            effective_date=eff,
            before=None,
            after=None,
            raw_excerpt=_excerpt(full_text, m, window=400),
        ))

    # ---- Subscription / redemption halt (an addendum event, not a field change) ----
    m = _HALT_RE.search(full_text)
    if m:
        eff = _extract_effective_date(full_text[max(0, m.start()-200):m.end()+300], doc_date)
        _add(ChangeSignal(
            event_type="addendum",
            effective_date=eff,
            before=None,
            after="subscription_suspension" if "subscription" in m.group(0).lower() else "redemption_suspension",
            raw_excerpt=_excerpt(full_text, m, window=300),
        ))

    # If we found no specific signals but the document is clearly an addendum,
    # record it as a generic 'addendum' event so it shows up in list_disclosure_events.
    if not signals:
        signals.append(ChangeSignal(
            event_type="addendum",
            effective_date=doc_date,
            before=None,
            after=None,
            raw_excerpt=full_text[:300].replace("\n", " ").strip(),
            confidence="official",
        ))

    return signals
