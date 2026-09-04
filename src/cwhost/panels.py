"""CodeWarrior PPC compiler preference records.

The four records are the compiler-side preference records the plug-in reads
through ``CWGetNamedPreferences``.  Field names are the IDE's XML setting
names (CodeWarrior "C Compilers Reference"); offsets and defaults were
recovered from project files on the CodeWarrior Pro 8 CD (see ``defaults``).
This module does not parse MCP files; flag mapping lives in ``cwhost.cli.flags``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PANEL_NAMES = (
    "C/C++ Compiler",
    "C/C++ Warnings",
    "PPC Global Optimizer",
    "PPC CodeGen",
)


def _byte(value: Any, field: str) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer byte") from exc
    if not 0 <= number <= 0xFF:
        raise ValueError(f"{field} is outside byte range: {number}")
    return number


def _bool(value: Any, field: str) -> int:
    return _byte(value, field)


def _pascal(value: str, capacity: int, field: str) -> bytes:
    try:
        raw = str(value).encode("mac_roman")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} is not representable in MacRoman") from exc
    if len(raw) > capacity - 1:
        raise ValueError(f"{field} is longer than {capacity - 1} bytes")
    return bytes([len(raw)]) + raw + bytes(capacity - 1 - len(raw))


@dataclass
class CCompilerPanel:
    """C/C++ Compiler preference record, version 16 (0x140 bytes)."""

    MWFrontEnd_C_cplusplus: int = 1
    MWFrontEnd_C_checkprotos: int = 0
    MWFrontEnd_C_arm: int = 0
    MWFrontEnd_C_trigraphs: int = 0
    MWFrontEnd_C_onlystdkeywords: int = 0
    MWFrontEnd_C_enumsalwaysint: int = 0
    MWFrontEnd_C_mpwpointerstyle: int = 0
    MWFrontEnd_C_ansistrict: int = 0
    MWFrontEnd_C_mpwcnewline: int = 1
    MWFrontEnd_C_wchar_type: int = 1
    MWFrontEnd_C_enableexceptions: int = 0
    MWFrontEnd_C_dontreusestrings: int = 0
    MWFrontEnd_C_poolstrings: int = 0
    MWFrontEnd_C_dontinline: int = 0
    MWFrontEnd_C_useRTTI: int = 0
    MWFrontEnd_C_multibyteaware: int = 0
    MWFrontEnd_C_unsignedchars: int = 0
    MWFrontEnd_C_autoinline: int = 0
    MWFrontEnd_C_booltruefalse: int = 0
    MWFrontEnd_C_inlinelevel: int = 0
    MWFrontEnd_C_ecplusplus: int = 0
    MWFrontEnd_C_objective_c: int = 0
    MWFrontEnd_C_defer_codegen: int = 0
    MWFrontEnd_C_templateparser: int = 0
    MWFrontEnd_C_c99: int = 0
    MWFrontEnd_C_bottomupinline: int = 1
    MWFrontEnd_C_prefixname: str = "MacHeadersCarbon.h"

    def to_bytes(self) -> bytes:
        data = bytearray(0x140)
        data[0:2] = (16).to_bytes(2, "big")
        offsets = (
            (0x02, "MWFrontEnd_C_cplusplus"),
            (0x03, "MWFrontEnd_C_checkprotos"),
            (0x04, "MWFrontEnd_C_arm"),
            (0x05, "MWFrontEnd_C_trigraphs"),
            (0x06, "MWFrontEnd_C_onlystdkeywords"),
            (0x07, "MWFrontEnd_C_enumsalwaysint"),
            (0x08, "MWFrontEnd_C_mpwpointerstyle"),
            (0x29, "MWFrontEnd_C_ansistrict"),
            (0x2A, "MWFrontEnd_C_mpwcnewline"),
            (0x2B, "MWFrontEnd_C_wchar_type"),
            (0x2C, "MWFrontEnd_C_enableexceptions"),
            (0x2D, "MWFrontEnd_C_dontreusestrings"),
            (0x2E, "MWFrontEnd_C_poolstrings"),
            (0x2F, "MWFrontEnd_C_dontinline"),
            (0x30, "MWFrontEnd_C_useRTTI"),
            (0x31, "MWFrontEnd_C_multibyteaware"),
            (0x32, "MWFrontEnd_C_unsignedchars"),
            (0x33, "MWFrontEnd_C_autoinline"),
            (0x34, "MWFrontEnd_C_booltruefalse"),
            (0x3A, "MWFrontEnd_C_ecplusplus"),
            (0x3B, "MWFrontEnd_C_objective_c"),
            (0x3C, "MWFrontEnd_C_defer_codegen"),
            (0x3D, "MWFrontEnd_C_templateparser"),
            (0x3E, "MWFrontEnd_C_c99"),
            (0x3F, "MWFrontEnd_C_bottomupinline"),
        )
        for offset, name in offsets:
            data[offset] = _bool(getattr(self, name), name)
        inline = int(self.MWFrontEnd_C_inlinelevel)
        if not -0x8000 <= inline <= 0x7FFF:
            raise ValueError("MWFrontEnd_C_inlinelevel is outside signed 16-bit range")
        data[0x38:0x3A] = inline.to_bytes(2, "big", signed=True)
        prefix = _pascal(self.MWFrontEnd_C_prefixname, 0x100, "MWFrontEnd_C_prefixname")
        data[0x40:0x140] = prefix
        # CW 8.x also mirrors a nonempty prefix in the legacy 32-byte area
        # at +09.  It is not an XML setting, but it is serialized by the
        # actual panel record and must be retained for byte-identical MCP
        # round-trips (the recovered map labels the area reserved).
        if self.MWFrontEnd_C_prefixname:
            data[0x09:0x29] = _pascal(
                self.MWFrontEnd_C_prefixname, 0x20, "MWFrontEnd_C_prefixname"
            )
        return bytes(data)


@dataclass
class CWarningsPanel:
    """C/C++ Warnings preference record, version 5 (14 bytes)."""

    MWWarning_C_warn_illpragma: int = 1
    MWWarning_C_warn_emptydecl: int = 1
    MWWarning_C_warn_possunwant: int = 1
    MWWarning_C_warn_unusedvar: int = 1
    MWWarning_C_warn_unusedarg: int = 1
    MWWarning_C_warn_extracomma: int = 1
    MWWarning_C_pedantic: int = 1
    MWWarning_C_warningerrors: int = 0
    MWWarning_C_warn_hidevirtual: int = 1
    MWWarning_C_warn_implicitconv: int = 0
    MWWarning_C_warn_notinlined: int = 0
    MWWarning_C_warn_structclass: int = 1

    _FIELDS = (
        "MWWarning_C_warn_illpragma",
        "MWWarning_C_warn_emptydecl",
        "MWWarning_C_warn_possunwant",
        "MWWarning_C_warn_unusedvar",
        "MWWarning_C_warn_unusedarg",
        "MWWarning_C_warn_extracomma",
        "MWWarning_C_pedantic",
        "MWWarning_C_warningerrors",
        "MWWarning_C_warn_hidevirtual",
        "MWWarning_C_warn_implicitconv",
        "MWWarning_C_warn_notinlined",
        "MWWarning_C_warn_structclass",
    )

    def to_bytes(self) -> bytes:
        data = bytearray((0, 5))
        data.extend(_bool(getattr(self, field), field) for field in self._FIELDS)
        return bytes(data)


@dataclass(init=False)
class PPCGlobalOptimizerPanel:
    """PPC Global Optimizer preference record, version 1 (12 bytes).

    ``level`` and ``optfor`` are accepted as the short names used by the
    panel's byte map, while the XML setting names remain available as
    attributes for consistency with the other records.
    """

    GlobalOptimizer_PPC_optimizationlevel: int = 4
    GlobalOptimizer_PPC_optfor: int | str = 0

    def __init__(self, level: int = 4, optfor: int | str = 0, **kwargs: Any) -> None:
        if "GlobalOptimizer_PPC_optimizationlevel" in kwargs:
            level = kwargs.pop("GlobalOptimizer_PPC_optimizationlevel")
        if "GlobalOptimizer_PPC_optfor" in kwargs:
            optfor = kwargs.pop("GlobalOptimizer_PPC_optfor")
        if kwargs:
            raise TypeError(f"unexpected optimizer fields: {', '.join(kwargs)}")
        self.GlobalOptimizer_PPC_optimizationlevel = level
        self.GlobalOptimizer_PPC_optfor = optfor

    def to_bytes(self) -> bytes:
        level = int(self.GlobalOptimizer_PPC_optimizationlevel)
        if not 0 <= level <= 4:
            raise ValueError("GlobalOptimizer_PPC_optimizationlevel must be in 0..4")
        optfor = self.GlobalOptimizer_PPC_optfor
        if isinstance(optfor, str):
            normalized = optfor.casefold()
            if normalized == "speed":
                optfor = 0
            elif normalized == "size":
                optfor = 1
            else:
                raise ValueError(
                    f"unknown optimizer target: {self.GlobalOptimizer_PPC_optfor!r}"
                )
        optfor = _byte(optfor, "GlobalOptimizer_PPC_optfor")
        if optfor not in (0, 1):
            raise ValueError("GlobalOptimizer_PPC_optfor must be Speed (0) or Size (1)")
        return bytes((0, 1, level, optfor)) + bytes(8)


_ALIGNMENT = {
    "mc68k": 0,
    "mac68k": 0,
    "mac68k4byte": 1,
    "natural": 2,
    "arraymembers": 3,
    "ppc_mw": 4,
    "powerpc": 4,
}
_TRACEBACK = {"none": 0, "inline": 1, "outofline": 2}
_PROCESSORS = {
    "generic": 0,
    "p601": 1,
    "p603": 2,
    "p603e": 3,
    "p604": 4,
    "p604e": 5,
    "p750": 6,
    "altivec": 7,
    "p7400": 8,
    "p7450": 9,
}
_FUNCTION_ALIGNMENT = {4: 2, 8: 3, 16: 4, 32: 2}


def _categorical(value: int | str, mapping: dict[str, int], field: str) -> int:
    if isinstance(value, str):
        key = value.casefold().replace(" ", "")
        if key not in mapping:
            raise ValueError(f"unknown {field}: {value!r}")
        return mapping[key]
    return _byte(value, field)


@dataclass
class PPCCodeGenPanel:
    """PPC CodeGen preference record, version 7 (34 bytes)."""

    MWCodeGen_PPC_structalignment: int | str = 4
    MWCodeGen_PPC_tracebacktables: int | str = 0
    MWCodeGen_PPC_processor: int | str = 0
    MWCodeGen_PPC_readonlystrings: int = 1
    MWCodeGen_PPC_tocdata: int = 1
    MWCodeGen_PPC_profiler: int = 0
    MWCodeGen_PPC_fpcontract: int = 1
    MWCodeGen_PPC_schedule: int = 1
    MWCodeGen_PPC_peephole: int = 1
    MWCodeGen_PPC_altivec: int = 0
    MWCodeGen_PPC_vectortocdata: int = 0
    MWCodeGen_PPC_poolconst: int = 0
    MWCodeGen_PPC_volatileasm: int = 0
    MWCodeGen_PPC_strictIEEEfp: int = 0
    MWCodeGen_PPC_genfsel: int = 0
    MWCodeGen_PPC_orderedfpcmp: int = 0
    MWCodeGen_PPC_altivec_move_block: int = 0
    MWCodeGen_PPC_function_align: int = 4
    MWCodeGen_PPC_largetoc: int = 0
    MWCodeGen_PPC_linkerpoolsstrings: int = 0

    def to_bytes(self) -> bytes:
        data = bytearray(0x22)
        data[:2] = (7).to_bytes(2, "big")
        data[0x02] = _categorical(
            self.MWCodeGen_PPC_structalignment, _ALIGNMENT, "structalignment"
        )
        data[0x03] = _categorical(
            self.MWCodeGen_PPC_tracebacktables, _TRACEBACK, "tracebacktables"
        )
        data[0x04] = _categorical(
            self.MWCodeGen_PPC_processor, _PROCESSORS, "processor"
        )
        for offset, field in (
            (0x05, "MWCodeGen_PPC_readonlystrings"),
            (0x06, "MWCodeGen_PPC_tocdata"),
            (0x07, "MWCodeGen_PPC_profiler"),
            (0x08, "MWCodeGen_PPC_fpcontract"),
            (0x09, "MWCodeGen_PPC_schedule"),
            (0x0A, "MWCodeGen_PPC_peephole"),
            (0x0C, "MWCodeGen_PPC_altivec"),
            (0x0D, "MWCodeGen_PPC_vectortocdata"),
            (0x11, "MWCodeGen_PPC_poolconst"),
            (0x12, "MWCodeGen_PPC_volatileasm"),
            (0x13, "MWCodeGen_PPC_strictIEEEfp"),
            (0x14, "MWCodeGen_PPC_genfsel"),
            (0x15, "MWCodeGen_PPC_orderedfpcmp"),
            (0x16, "MWCodeGen_PPC_altivec_move_block"),
            (0x19, "MWCodeGen_PPC_largetoc"),
            (0x1A, "MWCodeGen_PPC_linkerpoolsstrings"),
        ):
            data[offset] = _bool(getattr(self, field), field)
        function_align = int(self.MWCodeGen_PPC_function_align)
        try:
            data[0x17] = _FUNCTION_ALIGNMENT[function_align]
        except KeyError as exc:
            raise ValueError(f"unknown function alignment: {function_align}") from exc
        return bytes(data)


# The defaults are the ``Carbon Toolbox Final`` target in the CD's
# Mac OS Carbon C++ application stationery:
# ``.../(Project Stationery)/Mac OS C++/Mac OS Carbon/Mac OS Toolbox/C++ Toolbox Carbon/Toolbox C++ Carbon.mcp``.
# Its four records were decoded with mcpdump --all-pref; values below are
# transcribed fields, not copied raw bytes.
def defaults() -> dict[str, Any]:
    return {
        "C/C++ Compiler": CCompilerPanel(),
        "C/C++ Warnings": CWarningsPanel(),
        "PPC Global Optimizer": PPCGlobalOptimizerPanel(),
        "PPC CodeGen": PPCCodeGenPanel(),
    }


__all__ = [
    "PANEL_NAMES",
    "CCompilerPanel",
    "CWarningsPanel",
    "PPCCodeGenPanel",
    "PPCGlobalOptimizerPanel",
    "defaults",
]
