"""Schema snapshot test: diffs the live MCP tool schemas against a checked-in golden file.

MCP clients cache tool schemas (name, input parameters, types, defaults). A silent
argument rename, type change, or a `required` flag flip is invisible in every other test
here — the tool still "works" when called with the new shape, it just breaks every client
that cached the old one. This test makes that change loud instead of silent: any schema
diff fails CI with an explicit before/after, and updating the golden file is a deliberate,
reviewable action (`UPDATE_GOLDEN=1 pytest ...`), not something that happens by accident.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

GOLDEN_PATH = Path(__file__).parent.parent / "fixtures" / "mcp_tool_schemas.golden.json"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _current_schemas():
    from mcp.shared.memory import create_connected_server_and_client_session
    from indian_mf_mcp import server as server_mod

    async with create_connected_server_and_client_session(server_mod.mcp) as session:
        listed = await session.list_tools()
        out = {}
        for t in sorted(listed.tools, key=lambda x: x.name):
            out[t.name] = {
                "inputSchema": t.inputSchema,
                "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None,
            }
        return out


async def test_tool_schemas_match_golden_snapshot():
    current = await _current_schemas()

    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        pytest.skip("UPDATE_GOLDEN=1: wrote new golden snapshot, re-run without it to verify")

    golden = json.loads(GOLDEN_PATH.read_text())

    assert set(current) == set(golden), (
        f"Tool set changed. Added: {set(current) - set(golden)}, "
        f"removed: {set(golden) - set(current)}. If deliberate, re-run with UPDATE_GOLDEN=1."
    )
    for name in current:
        assert current[name] == golden[name], (
            f"Schema for tool {name!r} changed from the golden snapshot — this breaks any "
            "MCP client that cached the old shape. If deliberate, re-run with UPDATE_GOLDEN=1 "
            f"and review the diff in {GOLDEN_PATH} before committing it."
        )
