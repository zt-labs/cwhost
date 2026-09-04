"""Carbon date/time shims: UTCDateTime offset application."""

import pytest

from cwhost.carbon import TABLE
from cwhost.errors import HostError
from cwhost.session import Harness
from cwhost.vfs import VFS


def test_convert_utc_applies_tz_offset_seconds(monkeypatch):
    monkeypatch.setenv("CWHOST_TZ_OFFSET", "7200")
    h = Harness(VFS(build_root="/tmp"))
    g = h.guest
    source = g.alloc(8, 2)
    destination = g.alloc(8, 2)
    g.w16(source, 0)
    g.w32(source + 2, 1000)
    g.w16(source + 6, 0xABCD)
    g.set_gpr(3, source)
    g.set_gpr(4, destination)
    TABLE["ConvertUTCToLocalDateTime"](h, g)
    assert ((g.u16(destination) << 32) | g.u32(destination + 2)) == 8200
    assert g.u16(destination + 6) == 0xABCD


def test_convert_utc_offset_overflow_raises(monkeypatch):
    monkeypatch.setenv("CWHOST_TZ_OFFSET", "1")
    h = Harness(VFS(build_root="/tmp"))
    g = h.guest
    source = g.alloc(8, 2)
    destination = g.alloc(8, 2)
    g.w16(source, 0xFFFF)
    g.w32(source + 2, 0xFFFF_FFFF)
    g.w16(source + 6, 0)
    g.set_gpr(3, source)
    g.set_gpr(4, destination)
    with pytest.raises(HostError, match="timezone offset overflows UTCDateTime"):
        TABLE["ConvertUTCToLocalDateTime"](h, g)
