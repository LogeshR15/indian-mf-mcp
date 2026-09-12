"""Magic-byte format detection. Indian AMC sites lie about extension and Content-Type constantly
(spec §7) — never trust either."""
from __future__ import annotations

from enum import Enum


class FormatKind(str, Enum):
    XLSX = "xlsx"       # ZIP-based OOXML
    XLS_BIFF = "xls_biff"  # legacy binary Excel
    HTML = "html"       # some AMCs serve HTML mislabeled as .xls
    PDF = "pdf"
    UNKNOWN = "unknown"


def sniff(raw: bytes) -> FormatKind:
    if raw[:4] == b"PK\x03\x04":
        return FormatKind.XLSX
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return FormatKind.XLS_BIFF
    if raw[:5] == b"%PDF-":
        return FormatKind.PDF
    head = raw[:512].lstrip().lower()
    if head.startswith(b"<html") or head.startswith(b"<!doctype html") or b"<table" in head:
        return FormatKind.HTML
    return FormatKind.UNKNOWN
