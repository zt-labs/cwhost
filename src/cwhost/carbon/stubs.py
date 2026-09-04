"""Unexercised CarbonLib imports: bound at load, fail on first call."""

from __future__ import annotations

from typing import Any

from ..errors import UnimplementedImport
from . import TABLE, shim

STUBS = (
    "CharacterByteType",
    "ConvertFromTextToUnicode",
    "CreateTextToUnicodeInfo",
    "CreateTextToUnicodeInfoByEncoding",
    "DisposePtr",
    "DisposeTextToUnicodeInfo",
    "FSCloseFork",
    "FSCreateFileUnicode",
    "FSCreateFork",
    "FSDeleteObject",
    "FSGetDataForkName",
    "FSGetForkPosition",
    "FSGetForkSize",
    "FSMakeFSSpec",
    "FSOpenFork",
    "FSReadFork",
    "FSResolveAliasFileWithMountFlags",
    "FSSetForkPosition",
    "FSSetForkSize",
    "FSWriteFork",
    "FSpMakeFSRef",
    "Gestalt",
    "GetHandleSize",
    "HGetVol",
    "NewPtr",
    "PBCloseSync",
    "PBGetCatInfoSync",
    "PBGetEOFAsync",
    "PBGetEOFSync",
    "PBGetForkSizeAsync",
    "PBGetForkSizeSync",
    "PBHCreateSync",
    "PBHDeleteSync",
    "PBHGetVInfoSync",
    "PBHOpenDFSync",
    "PBReadAsync",
    "PBReadForkAsync",
    "PBReadForkSync",
    "PBReadSync",
    "PBSetCatInfoSync",
    "PBSetEOFAsync",
    "PBSetEOFSync",
    "PBSetFPosAsync",
    "PBSetFPosSync",
    "PBSetForkPositionAsync",
    "PBSetForkPositionSync",
    "PBSetForkSizeAsync",
    "PBSetForkSizeSync",
    "PBWriteAsync",
    "PBWriteForkAsync",
    "PBWriteForkSync",
    "PBWriteSync",
    "ReadLocation",
    "ResolveAliasFile",
    "UCCompareTextNoLocale",
    "num2dec",
    "rinttol",
    "roundtol",
)


def _register() -> None:
    for name in STUBS:
        if name in TABLE:
            raise ValueError(f"cannot stub already-registered Carbon shim {name}")

        def handler(session: Any, guest: Any, _name: str = name) -> None:
            raise UnimplementedImport(_name)

        shim(name)(handler)


_register()
