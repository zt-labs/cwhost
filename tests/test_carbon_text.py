"""Carbon Text Encoding Converter shims: accepted mappings and tokens."""

import pytest

from cwhost.carbon import TABLE
from cwhost.carbon.text import _UNICODE_MAPPING
from cwhost.errors import HostError
from cwhost.guest import Guest
from cwhost.session import Harness
from cwhost.vfs import VFS


def _harness():
    return Harness(VFS(build_root="/tmp"))


def _set_words(guest: Guest, values: list[int]) -> None:
    guest.set_gpr(1, Guest.STACK_TOP - 0x100)
    for index, value in enumerate(values):
        if index < 8:
            guest.set_gpr(3 + index, value)
        else:
            guest.w32(guest.gpr(1) + 24 + 4 * index, value)


def _create_info(h: Harness) -> int:
    g = h.guest
    mapping = g.alloc(12, 4)
    unicode_encoding, other_encoding, mapping_version = _UNICODE_MAPPING
    g.w32(mapping, unicode_encoding)
    g.w32(mapping + 4, other_encoding)
    g.w32(mapping + 8, mapping_version)
    out = g.alloc(4, 4)
    g.set_gpr(3, mapping)
    g.set_gpr(4, out)
    TABLE["CreateUnicodeToTextInfo"](h, g)
    return g.u32(out)


def test_unknown_info_token_raises():
    h = _harness()
    g = h.guest
    _set_words(g, [0xDEAD] + [0] * 11)
    with pytest.raises(HostError, match="unknown info token 0xdead"):
        TABLE["ConvertFromUnicodeToText"](h, g)


def test_unsupported_unicode_mapping_raises():
    h = _harness()
    g = h.guest
    mapping = g.alloc(12, 4)
    g.write(mapping, bytes(12))
    out = g.alloc(4, 4)
    g.set_gpr(3, mapping)
    g.set_gpr(4, out)
    with pytest.raises(HostError, match="unsupported UnicodeMapping"):
        TABLE["CreateUnicodeToTextInfo"](h, g)


def test_observed_unicode_mapping_creates_a_usable_token():
    h = _harness()
    g = h.guest
    token = _create_info(h)
    assert token in h.text_infos
    text = g.alloc(4, 2)
    g.write(text, "A".encode("utf-16-be"))
    dest = g.alloc(8, 1)
    _set_words(g, [token, 1, text, 0x20, 0, 0, 0, 0, 8, 0, 0, dest])
    TABLE["ConvertFromUnicodeToText"](h, g)
    assert g.read(dest, 1) == b"A"


def test_null_unicode_text_with_nonzero_length_raises():
    h = _harness()
    g = h.guest
    token = _create_info(h)
    dest = g.alloc(8, 1)
    _set_words(g, [token, 1, 0, 0x20, 0, 0, 0, 0, 8, 0, 0, dest])
    with pytest.raises(HostError, match=r"iUnicodeText is NULL with iUnicodeLen=1"):
        TABLE["ConvertFromUnicodeToText"](h, g)


def test_null_output_buf_with_nonzero_length_raises():
    h = _harness()
    g = h.guest
    token = _create_info(h)
    text = g.alloc(4, 2)
    g.write(text, "A".encode("utf-16-be"))
    _set_words(g, [token, 1, text, 0x20, 0, 0, 0, 0, 8, 0, 0, 0])
    with pytest.raises(HostError, match=r"oOutputBuf is NULL with iOutputBufLen=8"):
        TABLE["ConvertFromUnicodeToText"](h, g)


def test_upgrade_rejects_a_non_roman_script():
    h = _harness()
    g = h.guest
    out = g.alloc(4, 4)
    g.set_gpr(3, 1)
    g.set_gpr(4, 0xFF80)
    g.set_gpr(5, 0)
    g.set_gpr(6, 0)
    g.set_gpr(7, out)
    with pytest.raises(HostError, match=r"UpgradeScriptInfoToTextEncoding: script=1"):
        TABLE["UpgradeScriptInfoToTextEncoding"](h, g)


def test_disposed_info_token_is_rejected():
    h = _harness()
    g = h.guest
    token = _create_info(h)
    g.set_gpr(3, token)
    TABLE["DisposeUnicodeToTextInfo"](h, g)
    dest = g.alloc(8, 1)
    _set_words(g, [token, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, dest])
    with pytest.raises(HostError, match="unknown info token"):
        TABLE["ConvertFromUnicodeToText"](h, g)


def test_info_token_from_another_session_is_rejected():
    owner = _harness()
    token = _create_info(owner)
    other = _harness()
    g = other.guest
    dest = g.alloc(8, 1)
    _set_words(g, [token, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, dest])
    with pytest.raises(HostError, match="unknown info token"):
        TABLE["ConvertFromUnicodeToText"](other, g)
