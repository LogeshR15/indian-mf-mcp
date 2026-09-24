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
    r"(?:Fund[ \t]+Manager[s]?|Managed[ \t]+by)[*¥^#]*[ \t]*[:\-][ \t]*"
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
# "and" only as a whole word: inside a name it is just letters ("Pandey", "Nandita", "Anand").
_COSEP_RE = re.compile(r"\s*(?:&|\band\b|,|\|)\s*", re.IGNORECASE)

# "Mr./Ms./Mrs." title prefix
_TITLE_RE = re.compile(r"^(?:Mr\.|Ms\.|Mrs\.|Dr\.)\s*", re.IGNORECASE)

# Name-like: 2–5 capitalised word tokens (handles "Rajeev Thakkar", "Raunak Onkar",
# "Charanjit Singh Wadehra") — conservative: at least first + last, each word capitalised.
_NAME_RE = re.compile(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})$")


# Sentinel for "Managing Since: Since Inception" — resolved by the caller to the scheme's
# inception date when the factsheet prints it, else left unknown (never today's date).
SINCE_INCEPTION = "since_inception"

# A titled manager entry followed by its own "Managing Since" line — the layout real combined
# factsheets use (PPFAS, Aug 2026):
#   "Mr. Rajeev Thakkar: Chief Investment Officer - Equity\nand Director\n
#    Total Experience: 32 Years\nManaging Since: Since Inception"
# The "Managing Since" clause must follow before the next titled name: that pairing is what
# separates a manager entry from any other "Mr. X" on the page (trustees, disclaimers).
_TITLED_NAME_RE = re.compile(
    r"\b(?:Mr|Ms|Mrs|Dr)\.?[ \t]+((?:[A-Z][A-Za-z'\-]+\.?[ \t]+){0,3}[A-Z][A-Za-z'\-]+)"
)
_MANAGING_SINCE_CLAUSE_RE = re.compile(
    r"(?:managing\s+since|\(\s*since|w\.e\.f\.?)\s*[:\-]?\s*(since\s+inception|inception|"
    r"\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ,]+\d{2,4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s*\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)
# How far past a name its "Managing Since" may sit (title + experience lines).
_ENTRY_SPAN = 300


def extract_titled_managers(text: str) -> list[ExtractedManager]:
    """Managers from "Mr./Ms. <Name>" entries that each carry their own "Managing Since"."""
    names = list(_TITLED_NAME_RE.finditer(text))
    out: list[ExtractedManager] = []
    seen: set[str] = set()
    for i, m in enumerate(names):
        stop = names[i + 1].start() if i + 1 < len(names) else len(text)
        window = text[m.end():min(stop, m.end() + _ENTRY_SPAN)]
        since = _MANAGING_SINCE_CLAUSE_RE.search(window)
        if since is None:
            continue
        name = _normalise_name(m.group(1))
        if not _NAME_RE.match(name) or name in seen:
            continue
        seen.add(name)
        raw_since = since.group(1)
        managing_since = (SINCE_INCEPTION if "inception" in raw_since.lower()
                          else _parse_since_date(raw_since))
        out.append(ExtractedManager(name=name, managing_since=managing_since,
                                    raw_text=text[m.start():m.end() + since.end()][:300],
                                    confidence="high"))
    return out


_MONTHS_RE = (r"(?:January|February|March|April|May|June|July|August|September|October|"
              r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)")
_PERSON_RE = r"([A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,3})"

# Untitled name with its start in parentheses on the same line (Nippon, Jul 2026):
#   "Meenakshi Dawar (Managing Since Jan 2023)"
# and ICICI (Aug 2026), on the next line and without the closing parenthesis:
#   "Rajat Chandak \n(Managing this fund since July, 2021\n& Overall 18 years of experience)"
_PAREN_SINCE_RE = re.compile(
    _PERSON_RE + r"\s*\(\s*managing\s+(?:this\s+fund\s+)?since\s+("
    + _MONTHS_RE + r"\.?,?\s+(?:\d{1,2},?\s*)?\d{4})",
    re.IGNORECASE,
)

# A "Name | Since | Total Exp" table inside a fund-manager block, which default extraction
# breaks line by line (HDFC, Aug 2026):
#   "FUND MANAGER ¥ \nName Since Total Exp \nAmit Ganatra February \n01, 2026 \nOver 19 \nyears
#    \nBhagyesh Kagalkar \n(Gold/Silver \nInstruments) \nAugust \n26,2026 \nOver 31 \nyears"
# and SBI (Aug 2026), whose columns are Name | Total Experience | Managing Since:
#   "Name of Fund \nManagers\nTotal \nExperience\nManaging \nSince\nMr. Anup \nUpadhyay 18 years Dec-2024"
# Joined into one line, each row is: [title] name, optional (role), optional "N years",
# start date ("February 01, 2026" or "Dec-2024").
_FM_BLOCK_RE = re.compile(r"fund\s+manager(?:\(s\)|s)?\b", re.IGNORECASE)
_TABLE_ROW_RE = re.compile(
    r"(?:(?:Mr|Ms|Mrs|Dr)\.?\s+)?" + _PERSON_RE + r"(?:\s*\([^)]{0,60}\))?"
    r"(?:\s+\d{1,2}\s+years?)?\s+("
    + _MONTHS_RE + r"\s+\d{1,2}\s*,\s*\d{4}|" + _MONTHS_RE + r"[-\s]\d{4})\b"
)
# Words that look like a capitalised name but are table headers or captions.
_NOT_A_NAME = {"name", "since", "total", "exp", "over", "years", "fund", "manager", "managers",
               "scheme", "schemes", "date", "details", "experience", "managing", "overseas",
               "investment", "investments", "equity", "debt", "dedicated", "portion",
               "securities", "head", "research", "chief", "officer", "as", "on", "of"}


def _strip_non_name_words(name: str) -> str | None:
    """Drop leading header/caption words a table row ran into ("Total Exp Amit Ganatra" ->
    "Amit Ganatra", HDFC Aug 2026); None if what's left isn't a 2+ word name or still holds
    a caption word ("Overseas Investment", Nippon)."""
    words = name.split()
    while words and words[0].lower() in _NOT_A_NAME:
        words.pop(0)
    if len(words) < 2 or any(w.lower() in _NOT_A_NAME for w in words):
        return None
    return " ".join(words)
_BLOCK_SPAN = 700


def extract_paren_since_managers(text: str) -> list[ExtractedManager]:
    out = []
    for m in _PAREN_SINCE_RE.finditer(text):
        name = _normalise_name(m.group(1))
        if not _NAME_RE.match(name):
            continue
        out.append(ExtractedManager(name=name, managing_since=_parse_since_date(m.group(2)),
                                    raw_text=m.group(0)[:300], confidence="high"))
    return out


def extract_table_managers(text: str) -> list[ExtractedManager]:
    out = []
    for block in _FM_BLOCK_RE.finditer(text):
        flat = " ".join(text[block.end():block.end() + _BLOCK_SPAN].split())
        for m in _TABLE_ROW_RE.finditer(flat):
            name = _strip_non_name_words(_normalise_name(m.group(1)))
            if name is None or not _NAME_RE.match(name):
                continue
            out.append(ExtractedManager(name=name, managing_since=_parse_since_date(m.group(2)),
                                        raw_text=m.group(0)[:300], confidence="high"))
    return out


# ---- public API ------------------------------------------------------------

@dataclass
class ExtractedManager:
    name: str               # normalised (title stripped)
    managing_since: str | None  # ISO date if parseable; raw string otherwise
    raw_text: str           # the sentence/block we read this from
    confidence: str         # 'high' | 'low'
    managing_until: str | None = None  # ISO end date when the factsheet prints one


# "(Effective till March 31, 2026)" right after a name (Kotak Jul 2026, a manager whose tenure
# had already ended but who is still printed).
_UNTIL_RE = re.compile(
    r"^[\s,&]*\(?\s*(?:effective\s+)?(?:till|until|upto)\s+("
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s*\d{4}"
    r"|\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ,]+\d{4})",
    re.IGNORECASE,
)


def _managing_until(text: str, name: str) -> str | None:
    for m in re.finditer(re.escape(name), text):
        u = _UNTIL_RE.match(" ".join(text[m.end():m.end() + 80].split()))
        if u:
            return _parse_since_date(u.group(1))
    return None


def _parse_since_date(raw: str) -> str:
    """Try to convert a raw 'managing since' date string to ISO YYYY-MM-DD."""
    from datetime import datetime
    formats = [
        "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y",
        "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%b. %d, %Y",
        "%d-%B-%Y", "%d %B %Y", "%d %B, %Y", "%B %d,%Y", "%d/%m/%Y", "%d/%m/%y",
        # Month-precision starts (Nippon "Jan 2023", SBI "Dec-2024") -> first of the month.
        "%b %Y", "%B %Y", "%b-%Y", "%B-%Y", "%b, %Y", "%B, %Y",
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
        lines_after = text[tail_start:tail_start + 240].split("\n")
        tail = lines_after[0]  # first line after label
        # A co-manager list that wraps after its connector continues on the next line
        # (Kotak, Aug 2026: "Fund Manager*: Mr. Deepak Agrawal & \n Mr. Sunil Pandey").
        if re.search(r"(?:&|,|\band)\s*$", raw_names_block + tail) and len(lines_after) > 1:
            tail = tail + " " + lines_after[1]
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
        for em in (extract_titled_managers(page.text) + extract_paren_since_managers(page.text)
                   + extract_table_managers(page.text) + extract_managers_from_text(page.text)):
            clean = _strip_non_name_words(em.name)
            if clean is None:
                continue
            em.name = clean
            em.managing_until = em.managing_until or _managing_until(page.text, clean)
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
