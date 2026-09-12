"""Shared helper for AMCs that publish one combined workbook per month covering every scheme,
with an "Index"-style sheet mapping scheme names to per-scheme sheet codes (SBI, Motilal
Oswal so far). Column positions for the index sheet vary by AMC (SBI: code then name;
Motilal Oswal: name then code) — detected from the index sheet's own header row rather than
assumed, same principle as the main holdings parser.
"""
from __future__ import annotations

import io

import openpyxl


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
                    or "short code" in t), None)
        if nc is not None and cc is not None:
            name_col, code_col, header_idx = nc, cc, i
            break
    if header_idx is None:
        return None

    target = scheme_hint.strip().lower()
    best = None
    for row in rows[header_idx + 1:]:
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
