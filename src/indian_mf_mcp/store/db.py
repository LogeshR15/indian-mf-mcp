"""SQLite connection + schema bootstrap."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from indian_mf_mcp import config


def _schema_sql() -> str:
    return (Path(__file__).parent / "schema.sql").read_text()


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    config.ensure_dirs()
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: readers never block on a writer and vice versa (the read path — server.py — and
    # the write path — the mf-mcp ingest CLI — are separate processes that need to coexist).
    # journal_mode is persisted in the DB file header, so this is a one-time cost after the
    # first call, not per-connection overhead.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(_schema_sql())
    return conn


@contextmanager
def connect(db_path: Path | None = None):
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_shared_read_conn: sqlite3.Connection | None = None


def get_shared_read_connection() -> sqlite3.Connection:
    """One long-lived, read-only-by-policy connection for the MCP read path.

    The tool layer opened (and re-bootstrapped the schema on) a fresh connection per call —
    on a single-process stdio server that throws away every OS-level page cache warmup and
    prepared-statement cache between calls for no reason, since the server never writes.

    `PRAGMA query_only = 1` enforces read-only at the connection level (any write raises
    sqlite3.OperationalError) — a runtime guarantee a future refactor can't accidentally
    break, rather than "we just happen to only call read functions on this connection".
    A large `mmap_size` lets the OS page-cache back reads directly instead of copying
    through SQLite's own page cache on top of that; `cache_size` keeps more hot pages
    resident in-process. Safe to reuse across requests: a single-process stdio MCP server
    processes one request at a time on one event loop, so there's no concurrent-connection
    contention to worry about here.
    """
    global _shared_read_conn
    if _shared_read_conn is None:
        conn = get_connection()
        conn.execute("PRAGMA query_only = 1")
        conn.execute("PRAGMA mmap_size = 268435456")  # 256MB
        conn.execute("PRAGMA cache_size = -65536")     # 64MB (negative = KiB)
        _shared_read_conn = conn
    else:
        # A writer (the CLI, in a separate process) may have committed since we last read;
        # WAL mode makes fresh commits visible to this connection automatically on the next
        # statement, no reconnect needed — no-op kept here only as a clarity anchor.
        pass
    return _shared_read_conn


def reset_shared_read_connection() -> None:
    """Close and drop the cached read connection. Test-only — lets a test point a fresh
    process-wide read connection at a different (or newly seeded) database file."""
    global _shared_read_conn
    if _shared_read_conn is not None:
        _shared_read_conn.close()
        _shared_read_conn = None
