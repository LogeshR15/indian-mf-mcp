"""Stateful parser for AMFI NAVAll.txt.

Format (verified against live file, 2026-09-12):
  - Semicolon-delimited.
  - Two column layouts exist in the wild and both are handled:
      8-column (current): "Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;
        Scheme Name;Plan;Option;Net Asset Value;Date"
      6-column (legacy):  "Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;
        Scheme Name;Net Asset Value;Date"
    NAV and Date are always the last two fields; Plan/Option are present only in the 8-column
    layout, and are blank there for discontinued plans AMFI never backfilled.
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

# Placeholders AMFI uses in the ISIN columns when there is no ISIN to report.
_ISIN_SENTINELS = {"", "-", "n/a", "na", "redeemed"}


@dataclass
class NavRow:
    scheme_code: str
    isin_div_payout_or_growth: str | None
    isin_div_reinvestment: str | None
    scheme_name: str
    nav: float | None
    date: str | None
    plan_raw: str | None  # "Plan" column, 8-column layout only (None = column absent/blank)
    option_raw: str | None  # "Option" column, 8-column layout only
    amc_name: str
    scheme_type: str  # e.g. "Open Ended Schemes"
    category: str  # e.g. "Equity Scheme"
    sub_category: str  # e.g. "Mid Cap Fund"
    raw_header_string: str  # the untouched section header line


def _clean_isin(value: str) -> str | None:
    text = value.strip()
    return None if text.lower() in _ISIN_SENTINELS else text


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
        scheme_code, isin_payout, isin_reinvest, scheme_name = fields[:4]
        # NAV and Date are the last two fields in both the 6- and 8-column layouts.
        nav_str, date_str = fields[-2], fields[-1]
        if len(fields) >= 8:
            plan_raw: str | None = fields[4].strip() or None
            option_raw: str | None = fields[5].strip() or None
        else:
            plan_raw = option_raw = None
        try:
            nav = float(nav_str)
        except ValueError:
            nav = None
        rows.append(
            NavRow(
                scheme_code=scheme_code.strip(),
                isin_div_payout_or_growth=_clean_isin(isin_payout),
                isin_div_reinvestment=_clean_isin(isin_reinvest),
                scheme_name=scheme_name.strip(),
                nav=nav,
                date=(date_str.strip() or None),
                plan_raw=plan_raw,
                option_raw=option_raw,
                amc_name=amc_name,
                scheme_type=scheme_type,
                category=category,
                sub_category=sub_category,
                raw_header_string=raw_header,
            )
        )
    return rows
