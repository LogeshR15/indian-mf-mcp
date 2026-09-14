"""Central configuration: filesystem locations, source URLs.

Freshness note: there is deliberately no TTL-gated "skip the fetch, it's still fresh"
logic anywhere in this codebase. Re-fetch cadence is left to the caller (a cron running
`mf-mcp ingest-navall` daily, a periodic `mf-mcp backfill-portfolio` run, etc.) — this
module doesn't try to guess how often that should happen per document type. What IS
enforced everywhere is content-addressed idempotency: blobstore.put() and every ingest
function key on sha256, so re-fetching a document that hasn't changed is a cheap no-op
write, never a redundant reparse. An earlier version of this file declared TTL_NAV /
TTL_PORTFOLIO / TTL_FACTSHEET / TTL_SID / TTL_AMC_REGISTRY constants that no ingest path
ever actually read — they implied a caching policy that didn't exist, so they were
removed rather than left to mislead. Real skip-the-HTTP-call-entirely TTL gating (as
opposed to skip-the-reparse, which idempotency already gives you) remains a legitimate
future enhancement if redundant network calls become a real cost.
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_HOME = Path(os.environ.get("INDIAN_MF_MCP_HOME", Path.home() / ".indian-mf-mcp"))
DB_PATH = DATA_HOME / "store.db"
BLOB_DIR = DATA_HOME / "blobs"
NAVALL_ARCHIVE_DIR = DATA_HOME / "navall_archive"

AMFI_NAVALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
AMFI_NAV_HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
AMFI_PORTFOLIO_REGISTRY_URL = "https://www.amfiindia.com/online-center/portfolio-disclosure"

USER_AGENT = "indian-mf-mcp/0.1 (research tool; contact via project README)"

DEFAULT_RISK_FREE_RATE_ANNUAL = 0.065  # stated, overridable constant (RBI T-bill proxy pending live source)


def ensure_dirs() -> None:
    DATA_HOME.mkdir(parents=True, exist_ok=True)
    BLOB_DIR.mkdir(parents=True, exist_ok=True)
    NAVALL_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
