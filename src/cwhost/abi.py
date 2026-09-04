"""Explicit big-endian ABI layouts crossing the PPC guest boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import HostError

_SCALARS: dict[str, tuple[int, int]] = {
    "u8": (1, 1),
    "s8": (1, 1),
    "u16": (2, 2),
    "s16": (2, 2),
    "u32": (4, 4),
    "s32": (4, 4),
    "u64": (8, 8),
    "s64": (8, 8),
    "ptr": (4, 4),
    "ostype": (4, 4),
    "Boolean": (1, 1),
}
_ARRAY_RE = re.compile(r"^(u8|s8|u16|s16|u32|s32|u64|s64|ptr|ostype|Boolean)\[(\d+)\]$")


@dataclass(frozen=True)
class _Type:
    size: int
    align: int
    kind: str
    base: str | None = None
    count: int = 0


class Struct:
    """A small, deterministic codec for C structures."""

    def __init__(self, name: str, packing: str, fields: list[tuple[str, Any]]):
        if packing not in ("mac68k", "power"):
            raise HostError(f"unknown ABI packing {packing!r}")
        self.name = name
        self.packing = packing
        self.fields = list(fields)
        self._layout: dict[str, tuple[int, _Type, Any]] = {}
        cursor = 0
        cap = 2 if packing == "mac68k" else 4
        for field_name, field_type in self.fields:
            if field_name in self._layout:
                raise HostError(f"duplicate field {field_name} in {name}")
            typ = self._resolve_type(field_type, cap)
            cursor = _align_up(cursor, typ.align)
            self._layout[field_name] = (cursor, typ, field_type)
            cursor += typ.size
        # The SDK's mac68k structures have two-byte aggregate alignment.  A
        # nested power struct keeps its own layout but does not promote the
        # containing mac68k aggregate beyond that packing's alignment.
        self.align = cap
        self.size = _align_up(cursor, self.align)

    @staticmethod
    def _resolve_type(field_type: Any, cap: int) -> _Type:
        if isinstance(field_type, Struct):
            return _Type(field_type.size, field_type.align, "struct")
        if not isinstance(field_type, str):
            raise HostError(f"invalid ABI field type {field_type!r}")
        match = _ARRAY_RE.match(field_type)
        if match:
            base, count_text = match.groups()
            count = int(count_text)
            if count < 0:
                raise HostError(f"negative ABI array length {field_type}")
            size, base_align = _SCALARS[base]
            return _Type(size * count, min(base_align, cap), "array", base, count)
        try:
            size, alignment = _SCALARS[field_type]
        except KeyError as error:
            raise HostError(f"unknown ABI field type {field_type!r}") from error
        return _Type(size, min(alignment, cap), "scalar", field_type)

    def offset(self, field: str) -> int:
        try:
            return self._layout[field][0]
        except KeyError as error:
            raise KeyError(f"{self.name}.{field}") from error

    def _field(self, field: str) -> tuple[int, _Type]:
        try:
            return self._layout[field][0:2]
        except KeyError as error:
            raise HostError(f"unknown field {self.name}.{field}") from error

    @staticmethod
    def _scalar_encode(kind: str, value: Any) -> bytes:
        if kind == "ostype":
            if isinstance(value, str):
                value = value.encode("mac_roman")
            if isinstance(value, (bytes, bytearray)):
                raw = bytes(value)
                if len(raw) != 4:
                    raise HostError(f"OSType must contain four bytes, got {raw!r}")
                return raw
            value = int(value)
        if kind == "Boolean":
            value = int(bool(value))
        size = _SCALARS[kind][0]
        signed = kind.startswith("s")
        return int(value).to_bytes(size, "big", signed=signed)

    @classmethod
    def _encode_value(cls, typ: _Type, value: Any) -> bytes:
        if typ.kind == "struct":
            if not isinstance(value, dict):
                raise HostError("nested ABI structures require a dict value")
            # This branch is replaced by the owning Struct's field encoder;
            # retaining it makes malformed direct uses fail clearly.
            raise HostError("nested ABI type has no owning codec")
        if typ.kind == "scalar":
            return cls._scalar_encode(typ.base or "", value)
        if typ.base == "u8" and isinstance(value, (bytes, bytearray)):
            raw = bytes(value)
            if len(raw) != typ.count:
                raise HostError(f"expected {typ.count} bytes, got {len(raw)}")
            return raw
        if not isinstance(value, (list, tuple)):
            raise HostError("ABI array values require a sequence")
        if len(value) != typ.count:
            raise HostError(f"expected {typ.count} array elements, got {len(value)}")
        return b"".join(cls._scalar_encode(typ.base or "", item) for item in value)

    @staticmethod
    def _scalar_decode(kind: str, raw: bytes) -> Any:
        if kind == "ostype":
            return bytes(raw)
        if kind == "Boolean":
            return raw[0]
        return int.from_bytes(raw, "big", signed=kind.startswith("s"))

    @classmethod
    def _decode_value(cls, typ: _Type, raw: bytes) -> Any:
        if typ.kind == "scalar":
            return cls._scalar_decode(typ.base or "", raw)
        if typ.kind == "array":
            elem_size = _SCALARS[typ.base or "u8"][0]
            if typ.base == "u8":
                return bytes(raw)
            return [
                cls._scalar_decode(typ.base or "", raw[i : i + elem_size])
                for i in range(0, len(raw), elem_size)
            ]
        raise HostError("nested ABI type requires its owning codec")

    def _encode_field(self, field_type: Any, typ: _Type, value: Any) -> bytes:
        if typ.kind != "struct":
            return self._encode_value(typ, value)
        if not isinstance(field_type, Struct):
            raise HostError(f"{self.name} nested field has invalid declaration")
        if not isinstance(value, dict):
            raise HostError(f"{self.name} nested field requires a dict")
        raw = bytearray(field_type.size)
        field_type._pack_bytes(raw, value)
        return bytes(raw)

    def _decode_field(self, field_type: Any, typ: _Type, raw: bytes) -> Any:
        if typ.kind != "struct":
            return self._decode_value(typ, raw)
        if not isinstance(field_type, Struct):
            raise HostError(f"{self.name} nested field has invalid declaration")
        return field_type._unpack_bytes(raw)

    def _pack_bytes(self, target: bytearray, values: dict[str, Any]) -> None:
        unknown = set(values) - set(self._layout)
        if unknown:
            raise HostError(
                f"unknown fields for {self.name}: {', '.join(sorted(unknown))}"
            )
        for field_name, (offset, typ, field_type) in self._layout.items():
            value = values.get(field_name, _default_value(typ))
            target[offset : offset + typ.size] = self._encode_field(
                field_type, typ, value
            )

    def _unpack_bytes(self, raw: bytes) -> dict[str, Any]:
        if len(raw) < self.size:
            raise HostError(f"short {self.name}: {len(raw)} < {self.size}")
        result: dict[str, Any] = {}
        for field_name, (offset, typ, field_type) in self._layout.items():
            result[field_name] = self._decode_field(
                field_type, typ, raw[offset : offset + typ.size]
            )
        return result

    def pack(self, guest: Any, addr: int, **values: Any) -> None:
        raw = bytearray(self.size)
        self._pack_bytes(raw, values)
        guest.write(addr, raw)

    def unpack(self, guest: Any, addr: int) -> dict[str, Any]:
        return self._unpack_bytes(guest.read(addr, self.size))

    def write_field(self, guest: Any, addr: int, field: str, value: Any) -> None:
        offset, typ = self._field(field)
        field_type = self._layout[field][2]
        guest.write(addr + offset, self._encode_field(field_type, typ, value))


def _align_up(value: int, align: int) -> int:
    return (value + align - 1) // align * align


def _default_value(typ: _Type) -> Any:
    if typ.kind == "scalar":
        return b"\0" * 4 if typ.base == "ostype" else 0
    if typ.kind == "array":
        return b"\0" * typ.count if typ.base == "u8" else [0] * typ.count
    return {}


# authority: Universal Interfaces 3.4.1 Files.h, HFSUniStr255 lines 69-73.
FSRef = Struct("FSRef", "power", [("hidden", "u8[80]")])
HFSUniStr255 = Struct(
    "HFSUniStr255", "power", [("length", "u16"), ("unicode", "u16[255]")]
)
Str63 = "u8[64]"
Str255 = "u8[256]"

# authority: Universal Interfaces 3.4.1 Files.h lines 646-651; align=mac68k.
FSSpec = Struct(
    "FSSpec", "mac68k", [("vRefNum", "s16"), ("parID", "s32"), ("name", Str63)]
)

# authority: CWPlugins.h lines 198-205 (CWPLUGIN_LONG_FILENAME_SUPPORT).
CWFileSpec = Struct(
    "CWFileSpec", "mac68k", [("parentDirRef", FSRef), ("filename", HFSUniStr255)]
)
# authority: CWPlugins.h lines 293-310.
CWFileInfo = Struct(
    "CWFileInfo",
    "mac68k",
    [
        ("fullsearch", "Boolean"),
        ("dependencyType", "s8"),
        ("isdependentoffile", "s32"),
        ("suppressload", "Boolean"),
        ("padding", "Boolean"),
        ("filedata", "ptr"),
        ("filedatalength", "s32"),
        ("filedatatype", "s16"),
        ("fileID", "s16"),
        ("filespec", CWFileSpec),
        ("alreadyincluded", "Boolean"),
        ("recordbrowseinfo", "Boolean"),
    ],
)
# authority: CWPlugins.h lines 366-374.
CWMessageRef = Struct(
    "CWMessageRef",
    "mac68k",
    [
        ("sourcefile", CWFileSpec),
        ("linenumber", "s32"),
        ("tokenoffset", "s16"),
        ("tokenlength", "s16"),
        ("selectionoffset", "s32"),
        ("selectionlength", "s32"),
    ],
)
# authority: CWPlugins.h lines 411-416.
CWNewTextDocumentInfo = Struct(
    "CWNewTextDocumentInfo",
    "mac68k",
    [("documentname", "ptr"), ("text", "ptr"), ("markDirty", "Boolean")],
)
# authority: DropInCompilerLinker.h lines 197-209.
CWBrowseOptions = Struct(
    "CWBrowseOptions",
    "mac68k",
    [
        ("recordClasses", "Boolean"),
        ("recordEnums", "Boolean"),
        ("recordMacros", "Boolean"),
        ("recordTypedefs", "Boolean"),
        ("recordConstants", "Boolean"),
        ("recordTemplates", "Boolean"),
        ("recordUndefinedFunctions", "Boolean"),
        ("reserved1", "s32"),
        ("reserved2", "s32"),
    ],
)
# authority: DropInCompilerLinker.h lines 216-224.
CWDependencyInfo = Struct(
    "CWDependencyInfo",
    "mac68k",
    [
        ("fileIndex", "s32"),
        ("fileSpec", CWFileSpec),
        ("fileSpecAccessType", "s16"),
        ("dependencyType", "s16"),
    ],
)
# authority: DropInCompilerLinker.h lines 226-242.
CWObjectData = Struct(
    "CWObjectData",
    "mac68k",
    [
        ("objectdata", "ptr"),
        ("browsedata", "ptr"),
        ("reserved1", "s32"),
        ("codesize", "s32"),
        ("udatasize", "s32"),
        ("idatasize", "s32"),
        ("compiledlines", "s32"),
        ("interfaceChanged", "Boolean"),
        ("reserved2", "s32"),
        ("compilecontext", "ptr"),
        ("dependencies", "ptr"),
        ("dependencyCount", "s16"),
        ("objectfile", "ptr"),
    ],
)
# authority: DropInCompilerLinker.h lines 245-289, Macintosh host branch.
CWTargetInfo = Struct(
    "CWTargetInfo",
    "mac68k",
    [
        ("outputType", "s16"),
        ("outfile", CWFileSpec),
        ("symfile", CWFileSpec),
        ("runfile", CWFileSpec),
        ("linkType", "s16"),
        ("canRun", "Boolean"),
        ("canDebug", "Boolean"),
        ("targetCPU", "ostype"),
        ("targetOS", "ostype"),
        ("outfileCreator", "ostype"),
        ("outfileType", "ostype"),
        ("debuggerCreator", "ostype"),
        ("runHelperCreator", "ostype"),
        ("linkAgainstFile", CWFileSpec),
    ],
)
# authority: CWPlugins.h lines 498-518.
DropInFlags = Struct(
    "DropInFlags",
    "mac68k",
    [
        ("rsrcversion", "s16"),
        ("dropintype", "ostype"),
        ("earliestCompatibleAPIVersion", "u16"),
        ("dropinflags", "u32"),
        ("edit_language", "ostype"),
        ("newestAPIVersion", "u16"),
    ],
)


# authority: Universal Interfaces 3.4.1 Files.h lines 441-468; align=mac68k.
IOParam = Struct(
    "IOParam",
    "mac68k",
    [
        ("qLink", "ptr"),
        ("qType", "s16"),
        ("ioTrap", "s16"),
        ("ioCmdAddr", "ptr"),
        ("ioCompletion", "ptr"),
        ("ioResult", "s16"),
        ("ioNamePtr", "ptr"),
        ("ioVRefNum", "s16"),
        ("ioRefNum", "s16"),
        ("ioVersNum", "s8"),
        ("ioPermssn", "s8"),
        ("ioMisc", "ptr"),
        ("ioBuffer", "ptr"),
        ("ioReqCount", "s32"),
        ("ioActCount", "s32"),
        ("ioPosMode", "s16"),
        ("ioPosOffset", "s32"),
    ],
)
# authority: Universal Interfaces 3.4.1 Files.h lines 469-494.
HFileParam = Struct(
    "HFileParam",
    "mac68k",
    [
        ("qLink", "ptr"),
        ("qType", "s16"),
        ("ioTrap", "s16"),
        ("ioCmdAddr", "ptr"),
        ("ioCompletion", "ptr"),
        ("ioResult", "s16"),
        ("ioNamePtr", "ptr"),
        ("ioVRefNum", "s16"),
        ("ioFRefNum", "s16"),
        ("ioFVersNum", "s8"),
        ("ioPermssn", "s8"),
        ("ioFDirIndex", "s16"),
        ("ioFlAttrib", "s8"),
        ("ioFlVersNum", "s8"),
        ("ioFlFndrInfo", "u8[16]"),
        ("ioDirID", "s32"),
        ("ioFlStBlk", "u16"),
        ("ioFlLgLen", "s32"),
        ("ioFlPyLen", "s32"),
        ("ioFlRStBlk", "u16"),
        ("ioFlRLgLen", "s32"),
        ("ioFlRPyLen", "s32"),
        ("ioFlCrDat", "u32"),
        ("ioFlMdDat", "u32"),
    ],
)
# authority: Universal Interfaces 3.4.1 Files.h lines 588-637.
CInfoPBRec_hFileInfo = Struct(
    "CInfoPBRec_hFileInfo",
    "mac68k",
    [
        ("qLink", "ptr"),
        ("qType", "s16"),
        ("ioTrap", "s16"),
        ("ioCmdAddr", "ptr"),
        ("ioCompletion", "ptr"),
        ("ioResult", "s16"),
        ("ioNamePtr", "ptr"),
        ("ioVRefNum", "s16"),
        ("ioFRefNum", "s16"),
        ("ioFVersNum", "s8"),
        ("ioPermssn", "s8"),
        ("ioFDirIndex", "s16"),
        ("ioFlAttrib", "s8"),
        ("ioFlVersNum", "s8"),
        ("ioFlFndrInfo", "u8[16]"),
        ("ioDirID", "s32"),
        ("ioFlStBlk", "u16"),
        ("ioFlLgLen", "s32"),
        ("ioFlPyLen", "s32"),
        ("ioFlRStBlk", "u16"),
        ("ioFlRLgLen", "s32"),
        ("ioFlRPyLen", "s32"),
        ("ioFlCrDat", "u32"),
        ("ioFlMdDat", "u32"),
        ("ioFlBkDat", "u32"),
        ("ioFlXFndrInfo", "u8[16]"),
        ("ioFlParID", "s32"),
        ("ioFlClpSiz", "s32"),
    ],
)
# authority: Universal Interfaces 3.4.1 Files.h lines 614-637.
CInfoPBRec_dirInfo = Struct(
    "CInfoPBRec_dirInfo",
    "mac68k",
    [
        ("qLink", "ptr"),
        ("qType", "s16"),
        ("ioTrap", "s16"),
        ("ioCmdAddr", "ptr"),
        ("ioCompletion", "ptr"),
        ("ioResult", "s16"),
        ("ioNamePtr", "ptr"),
        ("ioVRefNum", "s16"),
        ("ioFRefNum", "s16"),
        ("ioFVersNum", "s8"),
        ("ioPermssn", "s8"),
        ("ioFDirIndex", "s16"),
        ("ioFlAttrib", "s8"),
        ("ioFlVersNum", "s8"),
        ("ioDrUsrWds", "u8[16]"),
        ("ioDrDirID", "s32"),
        ("ioDrNmFls", "u16"),
        ("reservedDirectoryFields", "u8[18]"),
        ("ioDrCrDat", "u32"),
        ("ioDrMdDat", "u32"),
        ("ioDrBkDat", "u32"),
        ("ioDrFndrInfo", "u8[16]"),
        ("ioDrParID", "s32"),
    ],
)
# authority: Universal Interfaces 3.4.1 Files.h lines 495-509.
HVolumeParam = Struct(
    "HVolumeParam",
    "mac68k",
    [
        ("qLink", "ptr"),
        ("qType", "s16"),
        ("ioTrap", "s16"),
        ("ioCmdAddr", "ptr"),
        ("ioCompletion", "ptr"),
        ("ioResult", "s16"),
        ("ioNamePtr", "ptr"),
        ("ioVRefNum", "s16"),
        ("ioFRefNum", "s16"),
        ("ioFVersNum", "s8"),
        ("ioVolIndex", "s16"),
        ("ioVCrDate", "u32"),
        ("ioVLsMod", "u32"),
        ("ioVAtrb", "s16"),
        ("ioVNmFls", "u16"),
        ("ioVBitMap", "u16"),
        ("ioAllocPtr", "u16"),
        ("ioVNmAlBlks", "u16"),
        ("ioVAlBlkSiz", "u32"),
        ("ioVClpSiz", "u32"),
        ("ioAlBlSt", "u16"),
        ("ioVNxtCNID", "u32"),
        ("ioVFrBlk", "u16"),
        ("ioVSigWord", "u16"),
        ("ioVDrvInfo", "s16"),
        ("ioVDRefNum", "s16"),
        ("ioVFSID", "s16"),
        ("ioVBkUp", "u32"),
        ("ioVSeqNum", "s16"),
        ("ioVWrCnt", "u32"),
        ("ioVFilCnt", "u32"),
        ("ioVDirCnt", "u32"),
        ("ioVFndrInfo", "u8[32]"),
    ],
)
# authority: Universal Interfaces 3.4.1 Files.h lines 5156-5184.
FSRefParam = Struct(
    "FSRefParam",
    "mac68k",
    [
        ("qLink", "ptr"),
        ("qType", "s16"),
        ("ioTrap", "s16"),
        ("ioCmdAddr", "ptr"),
        ("ioCompletion", "ptr"),
        ("ioResult", "s16"),
        ("ioNamePtr", "ptr"),
        ("ioVRefNum", "s16"),
        ("reserved1", "u16"),
        ("reserved2", "u8"),
        ("reserved3", "u8"),
        ("ref", "ptr"),
        ("whichInfo", "u32"),
        ("catInfo", "ptr"),
        ("nameLength", "u32"),
        ("name", "ptr"),
        ("ioDirID", "s32"),
        ("spec", "ptr"),
        ("parentRef", "ptr"),
        ("newRef", "ptr"),
        ("textEncodingHint", "u32"),
        ("outName", "ptr"),
    ],
)
# authority: Universal Interfaces 3.4.1 Files.h lines 5262-5284.
FSForkIOParam = Struct(
    "FSForkIOParam",
    "mac68k",
    [
        ("qLink", "ptr"),
        ("qType", "s16"),
        ("ioTrap", "s16"),
        ("ioCmdAddr", "ptr"),
        ("ioCompletion", "ptr"),
        ("ioResult", "s16"),
        ("reserved1", "ptr"),
        ("reserved2", "u16"),
        ("forkRefNum", "s16"),
        ("reserved3", "u8"),
        ("permissions", "u8"),
        ("ref", "ptr"),
        ("buffer", "ptr"),
        ("requestCount", "u32"),
        ("actualCount", "u32"),
        ("positionMode", "u16"),
        ("positionOffset", "s64"),
        ("allocationFlags", "u16"),
        ("allocationAmount", "u64"),
        ("forkNameLength", "u32"),
        ("forkName", "ptr"),
        ("forkIterator", "u8[16]"),
        ("outForkName", "ptr"),
    ],
)
# authority: Universal Interfaces 3.4.1 Files.h FSCatalogInfo declaration.
FSCatalogInfo = Struct(
    "FSCatalogInfo",
    "mac68k",
    [
        ("nodeFlags", "u16"),
        ("volume", "s16"),
        ("parentDirID", "u32"),
        ("nodeID", "u32"),
        ("sharingFlags", "u8"),
        ("userPrivileges", "u8"),
        ("reserved1", "u8"),
        ("reserved2", "u8"),
        ("createDate", "u8[8]"),
        ("contentModDate", "u8[8]"),
        ("attributeModDate", "u8[8]"),
        ("accessDate", "u8[8]"),
        ("backupDate", "u8[8]"),
        ("permissions", "u8[16]"),
        ("finderInfo", "u8[16]"),
        ("extFinderInfo", "u8[16]"),
        ("dataLogicalSize", "u64"),
        ("dataPhysicalSize", "u64"),
        ("rsrcLogicalSize", "u64"),
        ("rsrcPhysicalSize", "u64"),
        ("valence", "u32"),
        ("textEncodingHint", "u32"),
    ],
)

# Short aliases follow the names used by the Universal Interfaces headers.
FileParam = HFileParam
VolumeParam = HVolumeParam
CInfoHFileInfo = CInfoPBRec_hFileInfo
CInfoDirInfo = CInfoPBRec_dirInfo

# Union names used by callers of the Universal Interfaces declarations.
ParamBlockRec = IOParam
HParamBlockRec = HFileParam
CInfoPBRec = CInfoPBRec_hFileInfo
