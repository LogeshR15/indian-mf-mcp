"""Shared helper for AMCs that publish one combined workbook per month covering every scheme,
with an "Index"-style sheet mapping scheme names to per-scheme sheet codes (SBI, Motilal
Oswal, Tata, Nippon India so far). Column positions for the index sheet vary by AMC (SBI:
code then name; Motilal Oswal/Tata: name then code) — detected from the index sheet's own
header row rather than assumed, same principle as the main holdings parser. Nippon India's
Index sheet has no header row at all (just (code, name) pairs from row 0) — handled by a
headerless fallback that recognises the shape directly.
"""
from __future__ import annotations

import io

import openpyxl

# A sheet code is short and has no spaces (e.g. "ME", "YO08", "144D"); a scheme name is a
# much longer free-text string. Used only when no header row can be found at all.
_MAX_CODE_LEN = 10


def _match(rows, header_idx, name_col, code_col, scheme_hint) -> str | None:
    target = scheme_hint.strip().lower()
    best = None
    for row in rows[header_idx + 1 :]:
        if len(row) <= max(name_col, code_col):
            continue
        name, code = row[name_col], row[code_col]
        if not isinstance(name, str) or not code:
            continue
        name_l = name.strip().lower()
        if name_l == target:
            return str(code)
        if (target in name_l or name_l in target) and best is None:
            best = str(code)
    return best


def find_sheet_code(raw: bytes, scheme_hint: str, index_sheet_name: str = "Index") -> str | None:
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    if index_sheet_name not in wb.sheetnames:
        return None
    rows = list(wb[index_sheet_name].iter_rows(values_only=True))

    name_col = code_col = None
    header_idx = None
    for i, row in enumerate(rows):
        texts = [str(c).strip().lower() if isinstance(c, str) else "" for c in row]
        nc = next((idx for idx, t in enumerate(texts) if "fund name" in t or "scheme name" in t), None)
        cc = next((idx for idx, t in enumerate(texts) if "fund code" in t or "scheme code" in t
                    or "short code" in t or "short name" in t or "acronym" in t), None)
        if nc is not None and cc is not None:
            name_col, code_col, header_idx = nc, cc, i
            break

    if header_idx is not None:
        return _match(rows, header_idx, name_col, code_col, scheme_hint)

    # Headerless fallback (e.g. Nippon India): find the first row that looks like a
    # (short code, long name) pair and treat everything from there as data, no header to skip.
    for i, row in enumerate(rows):
        if len(row) < 2:
            continue
        code, name = row[0], row[1]
        if (isinstance(code, str) and isinstance(name, str) and " " not in code.strip()
                and 0 < len(code.strip()) <= _MAX_CODE_LEN and len(name.strip()) > len(code.strip())):
            return _match(rows, i - 1, 1, 0, scheme_hint)  # header_idx=i-1 so row i itself is included
    return None


def find_sheet_by_title(raw: bytes, scheme_hint: str, exclude_sheet_names: tuple[str, ...] = ()) -> str | None:
    """For AMCs with no separate Index/lookup sheet at all (e.g. Franklin Templeton): the
    sheet name itself IS the per-scheme code, and each sheet's own row 1 carries the full
    scheme name in its own first populated cell. Scans every sheet's title row instead of a
    shared lookup table."""
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    target = scheme_hint.strip().lower()
    best = None
    for sheet_name in wb.sheetnames:
        if sheet_name in exclude_sheet_names:
            continue
        ws = wb[sheet_name]
        try:
            row0 = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        except StopIteration:
            continue
        title = next((c for c in row0 if isinstance(c, str) and c.strip()), None)
        if title is None:
            continue
        title_l = title.strip().lower()
        if title_l == target:
            return sheet_name
        if (target in title_l or title_l in target) and best is None:
            best = sheet_name
    return best
