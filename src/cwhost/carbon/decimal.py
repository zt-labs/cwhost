"""Classic fp.h decimal conversion and rounding shims."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any

from . import shim

FE_INEXACT = 0x02000000
FE_DIVBYZERO = 0x04000000
FE_UNDERFLOW = 0x08000000
FE_OVERFLOW = 0x10000000
FE_INVALID = 0x20000000


def _flags(session: Any) -> int:
    return int(getattr(session, "fp_flags", 0))


def _set_flags(session: Any, flags: int) -> None:
    session.fp_flags = _flags(session) | flags


def _ret(guest: Any, value: int) -> int:
    guest.ret(value & 0xFFFF_FFFF)
    return value


@shim("dec2num")
def dec2num(session: Any, guest: Any) -> None:
    (source,) = guest.args(("ptr",))
    sign = guest.u8(source)
    exponent = int.from_bytes(guest.read(source + 2, 2), "big", signed=True)
    length = guest.u8(source + 4)
    text = guest.read(source + 5, length)
    try:
        value = float(Decimal(int(text or b"0")) * (Decimal(10) ** exponent))
        value = -value if sign & 0x80 else value
    except (InvalidOperation, ValueError, OverflowError):
        _set_flags(session, FE_INVALID)
        value = float("nan")
    if math.isinf(value):
        _set_flags(session, FE_OVERFLOW)
    guest.ret_f64(value)


@shim("feclearexcept")
def feclearexcept(session: Any, guest: Any) -> int:
    (mask,) = guest.args(("u32",))
    _set_flags(session, 0)
    session.fp_flags = _flags(session) & ~mask
    return _ret(guest, 0)


@shim("fetestexcept")
def fetestexcept(session: Any, guest: Any) -> int:
    (mask,) = guest.args(("u32",))
    return _ret(guest, _flags(session) & mask)
