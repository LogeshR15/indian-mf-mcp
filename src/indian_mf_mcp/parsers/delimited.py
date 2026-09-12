"""Stateful parser for AMFI's semicolon-delimited NAV files.

Two AMFI endpoints share this shape but *not* their column order, so parsing is driven by the
`Scheme Code;...` column-header line rather than by field position:

  NAVAll.txt (daily snapshot, verified live 2026-09-12):
    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;
    Net Asset Value;Date
  DownloadNAVHistoryReport_Po.aspx (date range, verified live 2026-09-13):
    Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;
    Net Asset Value;Date

A legacy 6-column NAVAll layout (no Plan/Option columns, plan and option embedded in the
scheme name) is still parsed for archived files. Positional parsing is used only as a
fallback for files with no column-header line at all.

Other structural rules, common to both files:
  - Section headers carry taxonomy, e.g. "Open Ended Schemes(Equity Scheme - Mid Cap Fund)"
  - Blank lines separate AMC blocks; the first non-blank, non-header, non-column-header line
    after a blank is the AMC name (a bare line with no ';').
  - Everything else is a data row.
  - Plan/Option are blank on discontinued plans AMFI never backfilled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_CATEGORY_RE = re.compile(r"^(.*?)\((.*)\)\s*$")
_COLUMN_HEADER_PREFIX = "Scheme Code;"
_WS_RE = re.compile(r"\s+")

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
    plan_raw: str | None  # "Plan" column; None when absent from the layout or blank
    option_raw: str | None  # "Option" column
    amc_name: str
    scheme_type: str  # e.g. "Open Ended Schemes"
    category: str  # e.g. "Equity Scheme"
    sub_category: str  # e.g. "Mid Cap Fund"
    raw_header_string: str  # the untouched section header line


AMFI_DATE_FMT = "%d-%b-%Y"


def parse_amfi_date(date_str: str | None) -> str | None:
    """AMFI dates look like '12-Sep-2026'. Returns an ISO date, or None if unparseable."""
    for fmt in (AMFI_DATE_FMT, "%d-%b-%y"):
        try:
            return datetime.strptime((date_str or "").strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _clean_isin(value: str | None) -> str | None:
    text = (value or "").strip()
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


def _parse_column_header(line: str) -> dict[str, int]:
    """Map logical field -> column index, from the file's own header line.

    Matching is on normalized column *names*, so AMFI reordering columns (as it did between
    NAVAll.txt and the history report) costs nothing here.
    """
    mapping: dict[str, int] = {}
    for idx, raw_name in enumerate(line.split(";")):
        name = _WS_RE.sub(" ", raw_name).strip().lower()
        if name == "scheme code":
            mapping["scheme_code"] = idx
        elif name in ("scheme name", "nav name"):
            mapping["scheme_name"] = idx
        elif name == "plan":
            mapping["plan"] = idx
        elif name == "option":
            mapping["option"] = idx
        elif name == "net asset value":
            mapping["nav"] = idx
        elif name == "date":
            mapping["date"] = idx
        elif "isin" in name:
            # "ISIN Div Payout/ ISIN Growth" mentions payout AND growth, so test reinvestment
            # first — it is the only unambiguous discriminator between the two ISIN columns.
            if "reinvest" in name:
                mapping["isin_reinvest"] = idx
            else:
                mapping["isin_payout"] = idx
    return mapping


# Field order of the legacy 6-column NAVAll layout, used only when a file has no header line.
_POSITIONAL_FALLBACK = {
    "scheme_code": 0, "isin_payout": 1, "isin_reinvest": 2, "scheme_name": 3,
}


def parse_navall(text: str) -> list[NavRow]:
    rows: list[NavRow] = []
    scheme_type = category = sub_category = raw_header = ""
    amc_name = ""
    expect_amc_name = False
    columns: dict[str, int] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip("\r\n")
        stripped = line.strip()

        if not stripped:
            expect_amc_name = True
            continue

        if stripped.startswith(_COLUMN_HEADER_PREFIX):
            columns = _parse_column_header(stripped)
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

        def field(key: str, default: str = "") -> str:
            idx = columns.get(key, _POSITIONAL_FALLBACK.get(key, -1))
            return fields[idx] if 0 <= idx < len(fields) else default

        if columns:
            nav_str, date_str = field("nav"), field("date")
        else:
            # Headerless file: NAV and Date are the last two fields in every known layout.
            nav_str, date_str = fields[-2], fields[-1]
        try:
            nav: float | None = float(nav_str)
        except ValueError:
            nav = None

        rows.append(
            NavRow(
                scheme_code=field("scheme_code").strip(),
                isin_div_payout_or_growth=_clean_isin(field("isin_payout")),
                isin_div_reinvestment=_clean_isin(field("isin_reinvest")),
                scheme_name=field("scheme_name").strip(),
                nav=nav,
                date=(date_str.strip() or None),
                plan_raw=(field("plan").strip() or None),
                option_raw=(field("option").strip() or None),
                amc_name=amc_name,
                scheme_type=scheme_type,
                category=category,
                sub_category=sub_category,
                raw_header_string=raw_header,
            )
        )
    return rows
