"""Stateful parser for AMFI NAVAll.txt.

Format (verified against live file, 2026-09-12):
  - Semicolon-delimited.
  - Column header line: "Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;
    Scheme Name;Net Asset Value;Date"
  - Section headers carry taxonomy, e.g. "Open Ended Schemes(Equity Scheme - Mid Cap Fund)"
  - Blank lines separate AMC blocks; the first non-blank, non-header, non-column-header line
    after a blank is the AMC name (a bare line with no ';').
  - Everything else is a data row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CATEGORY_RE = re.compile(r"^(.*?)\((.*)\)\s*$")
_COLUMN_HEADER_PREFIX = "Scheme Code;"


@dataclass
class NavRow:
    scheme_code: str
    isin_div_payout_or_growth: str | None
    isin_div_reinvestment: str | None
    scheme_name: str
    nav: float | None
    date: str | None
    amc_name: str
    scheme_type: str  # e.g. "Open Ended Schemes"
    category: str  # e.g. "Equity Scheme"
    sub_category: str  # e.g. "Mid Cap Fund"
    raw_header_string: str  # the untouched section header line


def _split_category(header: str) -> tuple[str, str, str, str]:
    """'Open Ended Schemes(Equity Scheme - Mid Cap Fund)' ->
    ('Open Ended Schemes', 'Equity Scheme', 'Mid Cap Fund', header)"""
    m = _CATEGORY_RE.match(header.strip())
    if not m:
        return header.strip(), "", "", header
    scheme_type = m.group(1).strip()
    inner = m.group(2).strip()
    if " - " in inner:
        category, sub_category = inner.split(" - ", 1)
    else:
        category, sub_category = inner, ""
    return scheme_type, category.strip(), sub_category.strip(), header


def parse_navall(text: str) -> list[NavRow]:
    rows: list[NavRow] = []
    scheme_type = category = sub_category = raw_header = ""
    amc_name = ""
    expect_amc_name = False

    for raw_line in text.splitlines():
        line = raw_line.strip("\r\n")
        stripped = line.strip()

        if not stripped:
            expect_amc_name = True
            continue

        if stripped.startswith(_COLUMN_HEADER_PREFIX):
            # column header row, not data
            continue

        if ";" not in stripped:
            # Either a section/category header or an AMC name.
            if "(" in stripped and stripped.endswith(")"):
                scheme_type, category, sub_category, raw_header = _split_category(stripped)
                expect_amc_name = True
            elif expect_amc_name:
                amc_name = stripped
                expect_amc_name = False
            else:
                # Unrecognized bare line; treat as AMC name defensively.
                amc_name = stripped
            continue

        fields = stripped.split(";")
        if len(fields) < 6:
            continue
        scheme_code, isin_payout, isin_reinvest, scheme_name, nav_str, date_str = fields[:6]
        try:
            nav = float(nav_str)
        except ValueError:
            nav = None
        rows.append(
            NavRow(
                scheme_code=scheme_code.strip(),
                isin_div_payout_or_growth=(isin_payout.strip() or None),
                isin_div_reinvestment=(isin_reinvest.strip() or None),
                scheme_name=scheme_name.strip(),
                nav=nav,
                date=(date_str.strip() or None),
                amc_name=amc_name,
                scheme_type=scheme_type,
                category=category,
                sub_category=sub_category,
                raw_header_string=raw_header,
            )
        )
    return rows
