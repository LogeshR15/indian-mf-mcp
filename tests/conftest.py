"""Auto-mark every *_live_smoke.py test as `network` (hits a real AMC/AMFI endpoint).

Excluded from the default run via `addopts = "-m 'not network'"` in pyproject.toml so
the default `pytest` invocation never flakes on a third-party site being slow/down/
redesigned. Run them explicitly with `pytest -m network` — that's also the signal that
should feed adapter-health monitoring, on a schedule, separate from CI-on-every-commit.
"""
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.fspath.basename.endswith("_live_smoke.py"):
            item.add_marker(pytest.mark.network)
