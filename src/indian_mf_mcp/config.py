"""Central configuration: filesystem locations, TTLs, source URLs."""
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

# TTLs in seconds
TTL_NAV = 24 * 3600
TTL_PORTFOLIO = 7 * 24 * 3600
TTL_FACTSHEET = 7 * 24 * 3600
TTL_SID = 90 * 24 * 3600
TTL_AMC_REGISTRY = 30 * 24 * 3600

USER_AGENT = "indian-mf-mcp/0.1 (research tool; contact via project README)"

DEFAULT_RISK_FREE_RATE_ANNUAL = 0.065  # stated, overridable constant (RBI T-bill proxy pending live source)


def ensure_dirs() -> None:
    DATA_HOME.mkdir(parents=True, exist_ok=True)
    BLOB_DIR.mkdir(parents=True, exist_ok=True)
    NAVALL_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
