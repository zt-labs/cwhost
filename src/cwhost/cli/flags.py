"""Map ``mwpefcc``-style flags onto compiler preference records."""

from __future__ import annotations

from typing import Any

from ..panels import _TRACEBACK, CWarningsPanel, defaults


def _need(argv: list[str], index: int, flag: str) -> str:
    if index + 1 >= len(argv):
        raise ValueError(f"{flag} requires an operand")
    return argv[index + 1]


def _onoff(value: str, flag: str) -> int:
    value = value.casefold()
    if value in {"on", "yes", "true", "1"}:
        return 1
    if value in {"off", "no", "false", "0"}:
        return 0
    raise ValueError(f"{flag} expects on|off, got {value!r}")


def _set_warning(panel: CWarningsPanel, name: str, value: int) -> None:
    aliases = {
        "illpragma": "MWWarning_C_warn_illpragma",
        "illpragmas": "MWWarning_C_warn_illpragma",
        "pragmas": "MWWarning_C_warn_illpragma",
        "emptydecl": "MWWarning_C_warn_emptydecl",
        "possible": "MWWarning_C_warn_possunwant",
        "possunwant": "MWWarning_C_warn_possunwant",
        "unwanted": "MWWarning_C_warn_possunwant",
        "unusedvar": "MWWarning_C_warn_unusedvar",
        "unusedarg": "MWWarning_C_warn_unusedarg",
        "extracomma": "MWWarning_C_warn_extracomma",
        "comma": "MWWarning_C_warn_extracomma",
        "pedantic": "MWWarning_C_pedantic",
        "extended": "MWWarning_C_pedantic",
        "iserror": "MWWarning_C_warningerrors",
        "error": "MWWarning_C_warningerrors",
        "hidevirtual": "MWWarning_C_warn_hidevirtual",
        "hiddenvirtual": "MWWarning_C_warn_hidevirtual",
        "implicitconv": "MWWarning_C_warn_implicitconv",
        "notinlined": "MWWarning_C_warn_notinlined",
        "structclass": "MWWarning_C_warn_structclass",
    }
    try:
        setattr(panel, aliases[name.casefold()], value)
    except KeyError as exc:
        raise ValueError(f"unknown warning option: {name!r}") from exc


def from_flags(argv: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Build compiler panels from ``mwpefcc``-style arguments.

    The starting state is the recovered Carbon application stationery profile.
    Unknown arguments, including source/output and include arguments, are
    returned in order for the CLI parser.
    """
    built = defaults()
    c = built["C/C++ Compiler"]
    w = built["C/C++ Warnings"]
    o = built["PPC Global Optimizer"]
    g = built["PPC CodeGen"]
    leftovers: list[str] = []
    i = 0
    while i < len(argv):
        flag = argv[i]
        low = flag.casefold()
        if low in {"-dialect", "-lang"}:
            value = _need(argv, i, flag).casefold()
            i += 2
            if value in {"c++", "cpp", "cxx"}:
                (
                    c.MWFrontEnd_C_cplusplus,
                    c.MWFrontEnd_C_ecplusplus,
                    c.MWFrontEnd_C_objective_c,
                    c.MWFrontEnd_C_c99,
                ) = 1, 0, 0, 0
            elif value in {"c", "ansi-c"}:
                (
                    c.MWFrontEnd_C_cplusplus,
                    c.MWFrontEnd_C_ecplusplus,
                    c.MWFrontEnd_C_objective_c,
                    c.MWFrontEnd_C_c99,
                ) = 0, 0, 0, 0
            elif value in {"ec++", "ecpp"}:
                (
                    c.MWFrontEnd_C_cplusplus,
                    c.MWFrontEnd_C_ecplusplus,
                    c.MWFrontEnd_C_objective_c,
                    c.MWFrontEnd_C_c99,
                ) = 1, 1, 0, 0
            elif value in {"objc", "objective-c", "objective_c"}:
                (
                    c.MWFrontEnd_C_cplusplus,
                    c.MWFrontEnd_C_ecplusplus,
                    c.MWFrontEnd_C_objective_c,
                    c.MWFrontEnd_C_c99,
                ) = 0, 0, 1, 0
            elif value == "c99":
                (
                    c.MWFrontEnd_C_cplusplus,
                    c.MWFrontEnd_C_ecplusplus,
                    c.MWFrontEnd_C_objective_c,
                    c.MWFrontEnd_C_c99,
                ) = 0, 0, 0, 1
            else:
                raise ValueError(f"unknown dialect: {value!r}")
            continue
        if low == "-requireprotos":
            c.MWFrontEnd_C_checkprotos = 1
            i += 1
            continue
        if low == "-relax_pointers":
            c.MWFrontEnd_C_mpwpointerstyle = 1
            i += 1
            continue
        if low == "-flag":
            value = _need(argv, i, flag)
            i += 2
            negative = value.casefold().startswith("no-")
            name = value[3:] if negative else value
            pragma = {
                "arm_conform": "MWFrontEnd_C_arm",
                "require_prototypes": "MWFrontEnd_C_checkprotos",
                "ansi_strict": "MWFrontEnd_C_ansistrict",
                "mpwc_newline": "MWFrontEnd_C_mpwcnewline",
            }.get(name.casefold())
            if pragma is None:
                raise ValueError(f"unknown compiler pragma: {name!r}")
            setattr(c, pragma, 0 if negative else 1)
            continue
        if low in {
            "-trigraphs",
            "-stdkeywords",
            "-wchar_t",
            "-cpp_exceptions",
            "-rtti",
            "-bool",
            "-iso_templates",
            "-profile",
            "-fp_contract",
            "-vector",
            "-volatileasm",
            "-strict_ieee",
            "-gen-fsel",
            "-ordered-fp-compares",
            "-altivec_move_block",
            "-largetoc",
            "-linkerpoolsstrings",
        }:
            value = _onoff(_need(argv, i, flag), flag)
            i += 2
            target = {
                "-trigraphs": (c, "MWFrontEnd_C_trigraphs"),
                "-stdkeywords": (c, "MWFrontEnd_C_onlystdkeywords"),
                "-wchar_t": (c, "MWFrontEnd_C_wchar_type"),
                "-cpp_exceptions": (c, "MWFrontEnd_C_enableexceptions"),
                "-rtti": (c, "MWFrontEnd_C_useRTTI"),
                "-bool": (c, "MWFrontEnd_C_booltruefalse"),
                "-iso_templates": (c, "MWFrontEnd_C_templateparser"),
                "-profile": (g, "MWCodeGen_PPC_profiler"),
                "-fp_contract": (g, "MWCodeGen_PPC_fpcontract"),
                "-vector": (g, "MWCodeGen_PPC_altivec"),
                "-volatileasm": (g, "MWCodeGen_PPC_volatileasm"),
                "-strict_ieee": (g, "MWCodeGen_PPC_strictIEEEfp"),
                "-gen-fsel": (g, "MWCodeGen_PPC_genfsel"),
                "-ordered-fp-compares": (g, "MWCodeGen_PPC_orderedfpcmp"),
                "-altivec_move_block": (g, "MWCodeGen_PPC_altivec_move_block"),
                "-largetoc": (g, "MWCodeGen_PPC_largetoc"),
                "-linkerpoolsstrings": (g, "MWCodeGen_PPC_linkerpoolsstrings"),
            }[low]
            setattr(*target, value)
            continue
        if low in {"-strict", "-mapcr"}:
            value = _onoff(_need(argv, i, flag), flag)
            i += 2
            setattr(
                c,
                "MWFrontEnd_C_ansistrict"
                if low == "-strict"
                else "MWFrontEnd_C_mpwcnewline",
                value,
            )
            continue
        if low in {"-nomapcr", "-no-mapcr"}:
            c.MWFrontEnd_C_mpwcnewline = 0
            i += 1
            continue
        if low in {"-char"}:
            value = _need(argv, i, flag).casefold()
            i += 2
            if value not in {"signed", "unsigned"}:
                raise ValueError("-char expects signed|unsigned")
            c.MWFrontEnd_C_unsignedchars = int(value == "unsigned")
            continue
        if low == "-enum":
            value = _need(argv, i, flag).casefold()
            i += 2
            if value not in {"int", "min"}:
                raise ValueError("-enum expects int|min")
            c.MWFrontEnd_C_enumsalwaysint = int(value == "int")
            continue
        if low == "-ansi":
            value = "on"
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                value = argv[i + 1]
                i += 1
            i += 1
            enabled = _onoff(value, flag) if value not in {"strict"} else 1
            c.MWFrontEnd_C_onlystdkeywords = enabled
            c.MWFrontEnd_C_enumsalwaysint = enabled
            c.MWFrontEnd_C_ansistrict = enabled
            continue
        if low == "-strings":
            value = _need(argv, i, flag).casefold()
            i += 2
            if value not in {"noreuse", "reuse", "pool", "nopool"}:
                raise ValueError("-strings expects noreuse|reuse|pool|nopool")
            if value in {"noreuse", "reuse"}:
                c.MWFrontEnd_C_dontreusestrings = int(value == "noreuse")
            else:
                c.MWFrontEnd_C_poolstrings = int(value == "pool")
            continue
        if low == "-inline":
            value = _need(argv, i, flag).casefold()
            i += 2
            if value.startswith("level="):
                c.MWFrontEnd_C_inlinelevel = int(value.split("=", 1)[1])
                c.MWFrontEnd_C_dontinline = 0
            elif value in {"none", "off"}:
                c.MWFrontEnd_C_dontinline, c.MWFrontEnd_C_autoinline = 1, 0
            elif value in {"auto", "noauto"}:
                c.MWFrontEnd_C_dontinline, c.MWFrontEnd_C_autoinline = (
                    0,
                    int(value == "auto"),
                )
            elif value in {"deferred", "defer"}:
                c.MWFrontEnd_C_defer_codegen = 1
            elif value in {"bottomup", "nobottomup", "no-bottomup"}:
                c.MWFrontEnd_C_bottomupinline = int(value == "bottomup")
            else:
                raise ValueError(f"unknown -inline option: {value!r}")
            continue
        if low in {"-w", "-warning"}:
            value = _need(argv, i, flag)
            i += 2
            negative = value.casefold().startswith("no")
            name = value[2:] if negative else value
            _set_warning(w, name, int(not negative))
            continue
        if low in {"-wall", "-werror", "-wunused"}:
            i += 1
            if low == "-werror":
                _set_warning(w, "iserror", 1)
            elif low == "-wunused":
                _set_warning(w, "unusedvar", 1)
                _set_warning(w, "unusedarg", 1)
            else:
                for name in (
                    "illpragma",
                    "emptydecl",
                    "possible",
                    "unusedvar",
                    "unusedarg",
                    "extracomma",
                    "pedantic",
                    "iserror",
                    "hidevirtual",
                    "implicitconv",
                    "notinlined",
                    "structclass",
                ):
                    _set_warning(w, name, 1)
            continue
        if low == "-opt":
            value = _need(argv, i, flag).casefold()
            i += 2
            if value.startswith("level"):
                raw = value.split("=", 1)[1] if "=" in value else value[5:]
                o.GlobalOptimizer_PPC_optimizationlevel = int(raw)
            elif value in {"off", "none"}:
                o.GlobalOptimizer_PPC_optimizationlevel = 0
            elif value in {"on"}:
                o.GlobalOptimizer_PPC_optimizationlevel = 1
            elif value == "all":
                o.GlobalOptimizer_PPC_optimizationlevel = 4
            elif value in {"space", "size"}:
                o.GlobalOptimizer_PPC_optfor = 1
            elif value == "speed":
                o.GlobalOptimizer_PPC_optfor = 0
            else:
                raise ValueError(f"unknown -opt option: {value!r}")
            continue
        if low in {"-align"}:
            value = _need(argv, i, flag).casefold()
            i += 2
            g.MWCodeGen_PPC_structalignment = value
            continue
        if low in {"-proc", "-processor", "-target"}:
            g.MWCodeGen_PPC_processor = _need(argv, i, flag)
            i += 2
            continue
        if low in {"-traceback"}:
            value = _need(argv, i, flag).casefold()
            i += 2
            if value not in _TRACEBACK:
                raise ValueError("-traceback expects none|inline|outofline")
            g.MWCodeGen_PPC_tracebacktables = value
            continue
        if low in {"-rostr", "-readonlystrings"}:
            g.MWCodeGen_PPC_readonlystrings = 1
            i += 1
            continue
        if low in {"-fwritable-strings"}:
            g.MWCodeGen_PPC_readonlystrings = 0
            i += 1
            continue
        if low == "-tocdata":
            g.MWCodeGen_PPC_tocdata = 1
            # The shipped command-line switch is also the only recovered
            # spelling that selects the vector-TOC byte in old records.
            g.MWCodeGen_PPC_vectortocdata = 1
            i += 1
            continue
        if low in {"-notocdata", "-no-tocdata"}:
            g.MWCodeGen_PPC_tocdata = 0
            g.MWCodeGen_PPC_vectortocdata = 0
            i += 1
            continue
        if low in {"-schedule", "-fschedule-insns"}:
            g.MWCodeGen_PPC_schedule = 1
            i += 1
            continue
        if low in {"-noschedule", "-fno-schedule-insns"}:
            g.MWCodeGen_PPC_schedule = 0
            i += 1
            continue
        if low == "-fpeephole":
            g.MWCodeGen_PPC_peephole = 1
            i += 1
            continue
        if low == "-fno-peephole":
            g.MWCodeGen_PPC_peephole = 0
            i += 1
            continue
        if low in {"-function-align", "-funcalign"}:
            value = int(_need(argv, i, flag))
            i += 2
            if value not in {4, 8, 16, 32}:
                raise ValueError("function alignment expects 4|8|16|32")
            g.MWCodeGen_PPC_function_align = value
            continue
        if low == "-compilerpoolsstrings":
            g.MWCodeGen_PPC_poolconst = 1
            i += 1
            continue
        if low == "-nocompilerpoolsstrings":
            g.MWCodeGen_PPC_poolconst = 0
            i += 1
            continue
        if low in {"-relax_ieee", "-relax-ieee"}:
            g.MWCodeGen_PPC_strictIEEEfp = 0
            i += 1
            continue
        if low in {"-no-relax_ieee", "-no-relax-ieee"}:
            g.MWCodeGen_PPC_strictIEEEfp = 1
            i += 1
            continue
        leftovers.append(flag)
        i += 1
    return built, leftovers
