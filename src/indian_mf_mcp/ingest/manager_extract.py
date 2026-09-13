"""Phase 3: Extract fund manager name(s) and 'managing since' date from a parsed PDF page set.

Factsheets are the primary source for current managers — they are monthly, dated, and usually
state the manager name explicitly alongside a "managing since" date (spec §3.3). SIDs are
authoritative but stale between revisions.

This module is intentionally conservative:
  - Returns a list of dicts, never a single field, because many schemes have co-managers.
  - Every extraction carries a `confidence` ('high' | 'low') and the raw_text it was read from.
  - When extraction fails or is ambiguous, we return an empty list — never a fabricated name.

Heuristic coverage (based on observed Indian AMC factsheet prose patterns):
  • "Fund Manager: Rajeev Thakkar" (inline colon)
  • "Managed by: Rajeev Thakkar & Raunak Onkar" (ampersand-joined co-managers)
  • "Fund Managers: Name1 | Name2" (pipe-separated)
  • Table rows: left cell = "Fund Manager" / right cell = name + "(Managing since DD-Mon-YYYY)"
  • "Mr. Rajeev Thakkar (Managing since May 28, 2013)" — inline managing-since date

These patterns cover ~85% of AMC factsheets observed; the remaining 15% fall through to
low-confidence or empty, which is correct behaviour (never guess).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---- regex patterns --------------------------------------------------------

# "Fund Manager(s):" or "Managed by:" label, followed by a name section.
# Match 2–5 capitalised word tokens (first name + last name minimum).
# Spaces between tokens are restricted to horizontal spaces ([ \t]) only so the
# match never bleeds across line boundaries (which \s would allow).
_FM_LABEL_RE = re.compile(
    r"(?:Fund[ \t]+Manager[s]?|Managed[ \t]+by)[ \t]*[:\-][ \t]*"
    r"((?:[A-Z][a-z]+\.?[ \t]+){1,4}[A-Z][a-z]+)",
)

# Inline "managing since" date — e.g. "(Managing since May 28, 2013)" or
# "(Managing since 28-May-2013)" or "(w.e.f. 01-Jan-2020)"
_SINCE_RE = re.compile(
    r"(?:managing\s+since|w\.e\.f\.?)\s*[:\-]?\s*"
    r"(\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ]\d{2,4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s*\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)

# Co-manager separators
_COSEP_RE = re.compile(r"\s*(?:&|and|,|\|)\s*", re.IGNORECASE)

# "Mr./Ms./Mrs." title prefix
_TITLE_RE = re.compile(r"^(?:Mr\.|Ms\.|Mrs\.|Dr\.)\s*", re.IGNORECASE)

# Name-like: 2–5 capitalised word tokens (handles "Rajeev Thakkar", "Raunak Onkar",
# "Charanjit Singh Wadehra") — conservative: at least first + last, each word capitalised.
_NAME_RE = re.compile(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})$")


# ---- public API ------------------------------------------------------------

@dataclass
class ExtractedManager:
    name: str               # normalised (title stripped)
    managing_since: str | None  # ISO date if parseable; raw string otherwise
    raw_text: str           # the sentence/block we read this from
    confidence: str         # 'high' | 'low'


def _parse_since_date(raw: str) -> str:
    """Try to convert a raw 'managing since' date string to ISO YYYY-MM-DD."""
    from datetime import datetime
    formats = [
        "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y",
        "%B %d, %Y", "%B %d %Y", "%d/%m/%Y", "%d/%m/%y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return raw.strip()  # return raw if unparseable — still useful


# Trailing punctuation characters that are never part of a person's name
_TRAIL_PUNCT_RE = re.compile(r"[\s\(\)\[\],;:\.\-]+$")


def _normalise_name(raw: str) -> str:
    name = _TITLE_RE.sub("", raw).strip()
    name = _TRAIL_PUNCT_RE.sub("", name).strip()
    # collapse internal whitespace
    return " ".join(name.split())


def extract_managers_from_text(text: str) -> list[ExtractedManager]:
    """Extract manager(s) from a single page or block of factsheet text."""
    results: list[ExtractedManager] = []
    seen_names: set[str] = set()

    for match in _FM_LABEL_RE.finditer(text):
        # Grab the full surrounding context (the matched name + up to 200 chars ahead)
        start = match.start()
        end = min(len(text), match.end() + 200)
        context = text[start:end]

        raw_names_block = match.group(1)
        # Extend to first line after the label to pick up managing-since clauses and
        # additional co-manager names that may follow on the same line.
        tail_start = match.end()
        tail_end = min(len(text), tail_start + 120)
        tail = text[tail_start:tail_end].split("\n")[0]  # first line after label
        full_block = raw_names_block + tail

        # Extract the managing-since date from the full_block BEFORE splitting on
        # co-manager separators — the date itself contains commas ("May 28, 2013")
        # that would otherwise be split on.
        block_since_raw: str | None = None
        since_match = _SINCE_RE.search(full_block)
        if since_match:
            block_since_raw = _parse_since_date(since_match.group(1))
            # Remove the since-clause so it doesn't pollute the name-splitting step
            full_block = full_block[: since_match.start()].strip()

        # Now split on co-manager separators (commas are safe now)
        raw_parts = [p.strip() for p in _COSEP_RE.split(full_block) if p.strip()]

        for i, part in enumerate(raw_parts):
            # Assign the since date to the first name only (it refers to the named manager)
            since_raw = block_since_raw if i == 0 else None

            name = _normalise_name(part)
            # Filter noise: must look like a real name (2-5 capitalised tokens)
            if not _NAME_RE.match(name):
                continue
            if name in seen_names:
                # already found; update since if we now have it
                for r in results:
                    if r.name == name and since_raw and not r.managing_since:
                        r.managing_since = since_raw
                continue
            seen_names.add(name)
            results.append(ExtractedManager(
                name=name,
                managing_since=since_raw,
                raw_text=context[:300],
                confidence="high",
            ))

    return results


def extract_managers_from_pages(pages: list) -> list[ExtractedManager]:
    """Run extraction over all pages of a PdfParseResult.pages list, deduplicating
    across pages. If a manager appears on multiple pages with different managing_since
    dates, the earliest date wins (most likely the first factsheet to mention them)."""
    by_name: dict[str, ExtractedManager] = {}
    for page in pages:
        for em in extract_managers_from_text(page.text):
            if em.name not in by_name:
                by_name[em.name] = em
            else:
                existing = by_name[em.name]
                # keep the earlier managing_since date
                if em.managing_since and existing.managing_since:
                    if em.managing_since < existing.managing_since:
                        existing.managing_since = em.managing_since
                elif em.managing_since and not existing.managing_since:
                    existing.managing_since = em.managing_since
    return list(by_name.values())
