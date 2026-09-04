import re
from pathlib import Path

import pytest

from cwhost import config
from cwhost.appledouble import AppleDouble
from cwhost.errors import HostError
from cwhost.vfs import VFS


def _stock(
    tmp_path: Path, *, resource: bytes | None = b"RSRC", sidecar: bool = False
) -> Path:
    root = tmp_path / "Metrowerks CodeWarrior"
    plugin = root / config.PLUGIN
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"PEF-data")
    for relative in config.SYSTEM_INCLUDES:
        (root / relative).mkdir(parents=True)
    if resource is not None:
        if sidecar:
            AppleDouble({2: resource}).write(plugin.parent / ("._" + plugin.name))
        else:
            VFS(root).node(plugin).write_resource_fork(resource)
    return root


def test_stock_root_resolves_every_path(tmp_path):
    root = _stock(tmp_path)
    toolchain = config.resolve(root)
    assert toolchain.plugin == (root / config.PLUGIN).resolve()
    assert toolchain.include_system == tuple(
        (root / relative).resolve() for relative in config.SYSTEM_INCLUDES
    )


def test_missing_root_names_the_path(tmp_path):
    missing = tmp_path / "absent"
    with pytest.raises(HostError, match=re.escape(str(missing.resolve()))):
        config.resolve(missing)


def test_missing_plugin_names_the_path(tmp_path):
    root = _stock(tmp_path)
    plugin = root / config.PLUGIN
    plugin.unlink()
    with pytest.raises(HostError, match=re.escape(str(plugin.resolve()))):
        config.resolve(root)


def test_plugin_without_native_resource_fork_names_the_path(tmp_path):
    root = _stock(tmp_path, resource=None)
    plugin = (root / config.PLUGIN).resolve()
    with pytest.raises(HostError, match=re.escape(str(plugin))):
        config.resolve(root)


def test_plugin_without_sidecar_resource_fork_names_the_path(tmp_path):
    root = _stock(tmp_path, resource=None)
    plugin = root / config.PLUGIN
    AppleDouble({9: b"\0" * 32}).write(plugin.parent / ("._" + plugin.name))
    with pytest.raises(HostError, match=re.escape(str(plugin.resolve()))):
        config.resolve(root)


def test_fixed_time(monkeypatch):
    monkeypatch.delenv("CWHOST_TIMESTAMP", raising=False)
    assert config.fixed_time() is None
    monkeypatch.setenv("CWHOST_TIMESTAMP", "1056470160")
    monkeypatch.setenv("CWHOST_TZ_OFFSET", "7200")
    assert config.fixed_time() == (1056470160, 7200)


def test_bad_timestamp_names_the_variable(monkeypatch):
    monkeypatch.setenv("CWHOST_TIMESTAMP", "not-a-time")
    with pytest.raises(HostError, match="CWHOST_TIMESTAMP"):
        config.fixed_time()
    monkeypatch.setenv("CWHOST_TIMESTAMP", "1")
    monkeypatch.setenv("CWHOST_TZ_OFFSET", "nope")
    with pytest.raises(HostError, match="CWHOST_TZ_OFFSET"):
        config.fixed_time()
