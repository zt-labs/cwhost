"""CarbonLib shim registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

TABLE: dict[str, Callable[[Any, Any], Any]] = {}
_F = TypeVar("_F", bound=Callable[[Any, Any], Any])


def shim(name: str, *, inline: bool = False):
    """Register a host shim under the imported Carbon symbol name."""
    if not name:
        raise ValueError("shim name must not be empty")

    def decorate(function: _F) -> _F:
        if name in TABLE:
            raise ValueError(f"duplicate Carbon shim {name}")
        if inline:
            cast(Any, function)._cwhost_inline_trap = True
        TABLE[name] = function
        return function

    return decorate


from . import decimal as _decimal  # noqa: F401
from . import fsref as _fsref  # noqa: F401
from . import memory_mgr as _memory_mgr  # noqa: F401
from . import resources_mgr as _resources_mgr  # noqa: F401
from . import stubs as _stubs  # noqa: F401
from . import system as _system  # noqa: F401
from . import text as _text  # noqa: F401
