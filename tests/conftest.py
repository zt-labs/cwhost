"""Portable fixtures. Plug-in tests skip unless a CodeWarrior root is configured."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cwhost import config


@pytest.fixture
def cw_root() -> Path:
    """The `Metrowerks CodeWarrior` folder named by CWHOST_CW_ROOT."""
    value = os.environ.get("CWHOST_CW_ROOT")
    if not value:
        pytest.skip("CWHOST_CW_ROOT is not set")
    root = Path(value).expanduser()
    if not root.is_dir():
        pytest.skip(f"CWHOST_CW_ROOT does not name a directory: {root}")
    return root


@pytest.fixture
def compiler_83(cw_root: Path) -> Path:
    plugin = cw_root / config.PLUGIN
    if not plugin.is_file():
        pytest.skip(f"compiler plug-in not present at {plugin}")
    return plugin
