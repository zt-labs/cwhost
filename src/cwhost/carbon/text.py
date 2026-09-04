"""Small MacRoman/Unicode subset of Carbon Text Encoding Converter."""

from __future__ import annotations

from typing import Any

from ..errors import HostError
from . import shim

K_TEXT_ENCODING_MAC_ROMAN = 0
# UnicodeConverter.h: kUnicodeLooseMappingsBit = 5.  The compiler sets this
# flag; this host has a single MacRoman mapping, which is the loose mapping.
K_UNICODE_LOOSE_MAPPINGS = 1 << 5

# Script.h / TextCommon.h values observed on every compiler call (``--trace``
# of a -dialect c compile of a trivial file, both 8.2 and 8.3):
# smRoman=0, kTextLanguageDontCare=-128, verUS=0, font name pointer 0.
_UPGRADE_SCRIPT = 0
_UPGRADE_LANGUAGE = -128
_UPGRADE_REGION = 0

# UnicodeMapping (UnicodeConverter.h) observed at the CreateUnicodeToTextInfo
# pointer: unicodeEncoding 0x00020103 (Unicode 2.1 + kUnicodeCanonicalDecompVariant
# + kUnicode16BitFormat), otherEncoding 0 (kTextEncodingMacRoman),
# mappingVersion 4 (kUnicodeUseHFSPlusMapping).
_UNICODE_MAPPING = (0x00020103, 0, 4)


def _encoding_info(session: Any, guest: Any):
    infos = getattr(session, "text_infos", None)
    if infos is None:
        infos = {}
        session.text_infos = infos
    return infos


@shim("GetScriptManagerVariable")
def GetScriptManagerVariable(session: Any, guest: Any) -> int:
    (selector,) = guest.args(("s16",))
    if selector not in (18, 40, 0):
        raise HostError(f"unsupported Script Manager selector {selector}")
    guest.ret(0)
    return 0


@shim("UpgradeScriptInfoToTextEncoding")
def UpgradeScriptInfoToTextEncoding(session: Any, guest: Any) -> int:
    script, language, region, font_name, output = guest.args(
        ("s16", "s16", "s16", "ptr", "ptr")
    )
    if (script, language, region, font_name) != (
        _UPGRADE_SCRIPT,
        _UPGRADE_LANGUAGE,
        _UPGRADE_REGION,
        0,
    ):
        raise HostError(
            "UpgradeScriptInfoToTextEncoding: "
            f"script={script} language={language} region={region} font={font_name:#x}"
        )
    if not output:
        raise HostError("UpgradeScriptInfoToTextEncoding: null encoding out-pointer")
    guest.w32(output, K_TEXT_ENCODING_MAC_ROMAN)
    guest.ret(0)
    return 0


@shim("CreateTextEncoding")
def CreateTextEncoding(session: Any, guest: Any) -> int:
    base, variant, fmt = guest.args(("u32", "u32", "u32"))
    value = base | ((variant & 0xFF) << 16) | ((fmt & 0xFF) << 24)
    guest.ret(value)
    return value


@shim("GetTextEncodingBase")
def GetTextEncodingBase(session: Any, guest: Any) -> int:
    (encoding,) = guest.args(("u32",))
    value = encoding & 0xFFFF
    guest.ret(value)
    return value


@shim("CreateUnicodeToTextInfo")
def CreateUnicodeToTextInfo(session: Any, guest: Any) -> int:
    mapping_ptr, output = guest.args(("ptr", "ptr"))
    if not mapping_ptr:
        raise HostError("CreateUnicodeToTextInfo: null UnicodeMapping")
    observed = (
        guest.u32(mapping_ptr),
        guest.u32(mapping_ptr + 4),
        guest.u32(mapping_ptr + 8),
    )
    if observed != _UNICODE_MAPPING:
        raise HostError(
            f"CreateUnicodeToTextInfo: unsupported UnicodeMapping {observed}"
        )
    if not output:
        raise HostError("CreateUnicodeToTextInfo: null info out-pointer")
    token = guest.alloc(4, 4)
    guest.w32(token, K_TEXT_ENCODING_MAC_ROMAN)
    guest.w32(output, token)
    _encoding_info(session, guest)[token] = K_TEXT_ENCODING_MAC_ROMAN
    guest.ret(0)
    return 0


@shim("DisposeUnicodeToTextInfo")
def DisposeUnicodeToTextInfo(session: Any, guest: Any) -> int:
    (token,) = guest.args(("ptr",))
    _encoding_info(session, guest).pop(token, None)
    guest.ret(0)
    return 0


def _conversion_args(guest: Any) -> tuple[Any, ...]:
    return tuple(
        guest.args(
            (
                "ptr",
                "u32",
                "ptr",
                "u32",
                "u32",
                "ptr",
                "ptr",
                "ptr",
                "u32",
                "ptr",
                "ptr",
                "ptr",
            )
        )
    )


@shim("ConvertFromUnicodeToText")
def ConvertFromUnicodeToText(session: Any, guest: Any) -> int:
    (
        info,
        input_len,
        input_ptr,
        control_flags,
        offset_count,
        _offset_array,
        offset_count_out,
        _offset_array_out,
        output_capacity,
        input_read,
        output_written,
        output_ptr,
    ) = _conversion_args(guest)
    if info not in _encoding_info(session, guest):
        raise HostError(f"ConvertFromUnicodeToText: unknown info token {info:#x}")
    if control_flags & ~K_UNICODE_LOOSE_MAPPINGS:
        raise HostError(
            f"ConvertFromUnicodeToText iControlFlags {control_flags:#x} is not implemented "
            "(UnicodeConverter.h kUnicode* bits)"
        )
    if offset_count:
        raise HostError("Unicode text conversion offset arrays are not implemented")
    if input_len and not input_ptr:
        raise HostError(
            f"ConvertFromUnicodeToText: iUnicodeText is NULL with iUnicodeLen={input_len}"
        )
    if output_capacity and not output_ptr:
        raise HostError(
            f"ConvertFromUnicodeToText: oOutputBuf is NULL with iOutputBufLen={output_capacity}"
        )
    text = (
        guest.read(input_ptr, input_len * 2).decode("utf-16-be")
        if input_ptr and input_len
        else ""
    )
    try:
        encoded = text.encode("mac_roman")
    except UnicodeEncodeError as error:
        raise HostError(
            f"Unicode text conversion cannot encode {text!r} as MacRoman"
        ) from error
    written = min(len(encoded), max(0, output_capacity))
    if output_ptr and written:
        guest.write(output_ptr, encoded[:written])
    if input_read:
        guest.w32(input_read, input_len)
    if output_written:
        guest.w32(output_written, written)
    if offset_count_out:
        guest.w32(offset_count_out, 0)
    guest.ret(0)
    return 0
