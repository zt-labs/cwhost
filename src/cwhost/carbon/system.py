"""Carbon system/date shims with explicit supported selectors."""

from __future__ import annotations

import time
from typing import Any

from .. import config
from ..errors import HostError
from . import shim

MAC_EPOCH = 2_082_844_800
# UTCDateTime / LocalDateTime (UTCUtils.h, Files.h): u16 highSeconds, u32 lowSeconds, u16 fraction.
_UTC_SECONDS_MASK = (1 << 48) - 1


def _ret(guest: Any, value: int) -> int:
    guest.ret(value & 0xFFFF_FFFF)
    return value


@shim("GetDateTime")
def GetDateTime(session: Any, guest: Any) -> int:
    (output,) = guest.args(("ptr",))
    fixed = config.fixed_time()
    value = int(time.time()) if fixed is None else fixed[0]
    guest.w32(output, value + MAC_EPOCH)
    return _ret(guest, 0)


@shim("ConvertUTCToLocalDateTime")
def ConvertUTCToLocalDateTime(session: Any, guest: Any) -> int:
    source, destination = guest.args(("ptr", "ptr"))
    high = guest.u16(source)
    low = guest.u32(source + 2)
    fraction = guest.u16(source + 6)
    local = ((high << 32) | low) + config.tz_offset()
    if not 0 <= local <= _UTC_SECONDS_MASK:
        raise HostError(
            "ConvertUTCToLocalDateTime: timezone offset overflows UTCDateTime"
        )
    guest.w16(destination, local >> 32)
    guest.w32(destination + 2, local & 0xFFFF_FFFF)
    guest.w16(destination + 6, fraction)
    return _ret(guest, 0)


@shim("TickCount", inline=True)
def TickCount(session: Any, guest: Any) -> int:
    value = int(time.monotonic() * 60) & 0xFFFF_FFFF
    return _ret(guest, value)
