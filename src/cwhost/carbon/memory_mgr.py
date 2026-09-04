"""Carbon Memory Manager imports used by the CodeWarrior plug-ins."""

from __future__ import annotations

import contextlib
from typing import Any

from ..memory import Handles, Heap
from . import shim


def _session_value(session: Any, name: str) -> Any:
    if isinstance(session, dict):
        return session.get(name)
    return getattr(session, name, None)


def _set_session_value(session: Any, name: str, value: Any) -> None:
    if isinstance(session, dict):
        session[name] = value
    else:
        with contextlib.suppress(AttributeError, TypeError):
            setattr(session, name, value)


def _handles(session: Any, guest: Any) -> Handles:
    for name in ("handles", "memory_handles"):
        value = _session_value(session, name)
        if isinstance(value, Handles):
            return value
    value = getattr(guest, "_cwhost_handles", None)
    if isinstance(value, Handles):
        return value
    manager = Handles(Heap(guest))
    if session is not None:
        _set_session_value(session, "handles", manager)
    if not isinstance(_session_value(session, "handles"), Handles):
        guest._cwhost_handles = manager
    return manager


def _args(guest: Any, *types: str) -> list[Any]:
    return guest.args(tuple(types))


def _ret(guest: Any, value: int) -> int:
    guest.ret(value)
    return value


@shim("NewHandle")
def NewHandle(session: Any, guest: Any) -> int:
    (size,) = _args(guest, "u32")
    return _ret(guest, _handles(session, guest).new(size))


@shim("DisposeHandle")
def DisposeHandle(session: Any, guest: Any) -> None:
    (handle,) = _args(guest, "ptr")
    _handles(session, guest).dispose(handle)
    guest.ret(0)


@shim("SetHandleSize")
def SetHandleSize(session: Any, guest: Any) -> None:
    handle, size = _args(guest, "ptr", "u32")
    _handles(session, guest).resize(handle, size)
    guest.ret(0)


@shim("HLock")
def HLock(session: Any, guest: Any) -> None:
    (handle,) = _args(guest, "ptr")
    _handles(session, guest).lock(handle)
    guest.ret(0)


@shim("HLockHi")
def HLockHi(session: Any, guest: Any) -> None:
    (handle,) = _args(guest, "ptr")
    _handles(session, guest).lock(handle)
    guest.ret(0)


@shim("HUnlock")
def HUnlock(session: Any, guest: Any) -> None:
    (handle,) = _args(guest, "ptr")
    _handles(session, guest).unlock(handle)
    guest.ret(0)


@shim("MemError")
def MemError(session: Any, guest: Any) -> int:
    value = _session_value(session, "mem_error")
    if value is None:
        value = 0
    return _ret(guest, int(value) & 0xFFFF_FFFF)


@shim("TempNewHandle")
def TempNewHandle(session: Any, guest: Any) -> int:
    size, result_code = _args(guest, "u32", "ptr")
    handle = _handles(session, guest).new(size)
    if result_code:
        guest.w16(result_code, 0)
    return _ret(guest, handle)


@shim("TempFreeMem")
def TempFreeMem(session: Any, guest: Any) -> int:
    # TempFreeMem has no arguments and reports available temporary memory.
    # The host uses one guest heap for both ordinary and temporary allocations,
    # so its free ranges are the truthful value to expose to the compiler.
    manager = _handles(session, guest)
    return _ret(guest, manager.heap.free_bytes)
