"""CodeWarrior installation layout and fixed clocks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import HostError

PLUGIN = Path("CodeWarrior Plugins/Compilers/MW C-C++ PPC")
SYSTEM_INCLUDES = (
    Path("MSL/MSL_C/MSL_Common/Include"),
    Path("MSL/MSL_C/MSL_MacOS/Include"),
    Path("MSL/MSL_C++/MSL_Common/Include"),
    Path("MSL/MSL_Extras/MSL_Common/Include"),
    Path("MSL/MSL_Extras/MSL_MacOS/Include"),
    Path("MacOS Support/Universal/Interfaces/CIncludes"),
)


@dataclass(frozen=True)
class Toolchain:
    root: Path
    plugin: Path
    include_system: tuple[Path, ...]


def cw_root() -> Path | None:
    value = os.environ.get("CWHOST_CW_ROOT")
    return Path(value).expanduser() if value else None


def _int_env(name: str, default: str | None = None) -> int:
    raw = os.environ.get(name, default)
    if raw is None:
        raise HostError(f"{name} is not set")
    try:
        return int(raw)
    except ValueError as error:
        raise HostError(f"invalid {name}: {raw!r}") from error


def tz_offset() -> int:
    """Seconds east of UTC (`CWHOST_TZ_OFFSET`, default 0)."""
    return _int_env("CWHOST_TZ_OFFSET", "0")


def fixed_time() -> tuple[int, int] | None:
    stamp = os.environ.get("CWHOST_TIMESTAMP")
    if stamp is None:
        return None
    return _int_env("CWHOST_TIMESTAMP"), tz_offset()


def _require_dir(path: Path) -> Path:
    if not path.is_dir():
        raise HostError(f"required path not found: {path}")
    return path


def _require_file(path: Path) -> Path:
    if not path.is_file():
        raise HostError(f"required path not found: {path}")
    return path


def resolve(cw_root: Path, *, nostdinc: bool = False) -> Toolchain:
    """Validate a stock Metrowerks CodeWarrior folder (spec §5)."""
    root = Path(cw_root).expanduser().resolve()
    _require_dir(root)
    plugin = _require_file(root / PLUGIN)
    if plugin.stat().st_size == 0:
        raise HostError(f"plug-in data fork is empty: {plugin}")
    from .vfs import VFS

    resource = VFS(plugin.parent).node(plugin).resource_fork()
    if not resource:
        raise HostError(f"plug-in has no resource fork: {plugin}")
    includes: tuple[Path, ...] = ()
    if not nostdinc:
        includes = tuple(_require_dir(root / relative) for relative in SYSTEM_INCLUDES)
    return Toolchain(root=root, plugin=plugin, include_system=includes)
