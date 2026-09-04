"""Command-line driver for the CodeWarrior C/C++ compiler plug-in."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NoReturn

from .. import config
from ..api import CompileOptions
from ..api import compile as compile_unit
from ..errors import HostError, PluginError, UnimplementedImport
from ..pluginlib import cwNoErr
from ..vfs import VFS
from .flags import from_flags

_USAGE = "cwcc [--cw-root PATH] [panel flags] [-I DIR]... [-ir DIR]... [-nostdinc] [-prefix FILE] [-E] [-g] -c SOURCE -o OUTPUT"

_EPILOG = """\
Panel flags use the mwpefcc spelling and set the compiler's preference records:
  -dialect c|c++|ec++|objc|c99   -requireprotos   -strict on|off   -mapcr on|off
  -bool on|off   -rtti on|off   -cpp_exceptions on|off   -iso_templates on|off
  -enum int|min   -char signed|unsigned   -strings noreuse|reuse|pool|nopool
  -inline level=N|none|auto|deferred|bottomup   -w NAME|noNAME   -wall   -werror
  -opt level=N|off|all|speed|space   -align powerpc|mac68k|...   -proc NAME
  -traceback none|inline|outofline   -rostr   -tocdata   -schedule   -fpeephole
  -function-align 4|8|16|32   -compilerpoolsstrings   -relax_ieee
Any flag not listed above or below is rejected.

The CodeWarrior root is the "Metrowerks CodeWarrior" folder of a Pro 8
installation; CWHOST_CW_ROOT supplies it when --cw-root is absent.
CWHOST_TIMESTAMP and CWHOST_TZ_OFFSET fix the clock the compiler observes.
"""


class _Parser(argparse.ArgumentParser):
    """argparse with errors surfaced as ValueError in cwcc's own wording."""

    def error(self, message: str) -> NoReturn:
        match = re.fullmatch(r"argument (\S+): (.*)", message)
        if match:
            flag, detail = match.groups()
            if detail == "expected one argument":
                detail = "requires a path"
            raise ValueError(f"{flag} {detail}")
        raise ValueError(message)


def _path(value: str) -> Path:
    if not value:
        raise argparse.ArgumentTypeError("requires a path")
    return Path(value).expanduser()


def _prefix(value: str) -> Path:
    # The recovered flag lines spell "no prefix" as a literal pair of quotes.
    if value in {"''", '""'}:
        value = ""
    return Path(value).expanduser() if value else Path("")


def _parser() -> _Parser:
    parser = _Parser(
        prog="cwcc",
        usage=_USAGE,
        description="Compile one C/C++ translation unit with the CodeWarrior Pro 8 PowerPC plug-in.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--cw-root", type=_path, metavar="PATH", help="CodeWarrior installation folder"
    )
    # No lowercase -i alias: it would make -irDIR ambiguous (see _parse).
    parser.add_argument(
        "-I",
        dest="include_user",
        type=_path,
        action="append",
        default=[],
        metavar="DIR",
        help="user include directory, searched before the source's directory",
    )
    parser.add_argument(
        "-ir",
        dest="include_system",
        type=_path,
        action="append",
        default=[],
        metavar="DIR",
        help="system include directory, searched before the MSL and Universal Interfaces",
    )
    parser.add_argument(
        "-nostdinc",
        action="store_true",
        help="do not add the root's MSL and Universal Interfaces",
    )
    parser.add_argument(
        "-prefix", type=_prefix, metavar="FILE", help='prefix header; "" for none'
    )
    parser.add_argument(
        "-E",
        "-e",
        dest="preprocess",
        action="store_true",
        help="write preprocessed text instead of an object",
    )
    parser.add_argument(
        "-g", dest="debug", action="store_true", help="generate debug information"
    )
    parser.add_argument(
        "--trace",
        type=_path,
        metavar="FILE",
        help="log every host callback as JSON lines",
    )
    parser.add_argument(
        "--trace-code",
        type=_path,
        metavar="FILE",
        help="log every executed guest block (slow)",
    )
    parser.add_argument(
        "-c", dest="source", type=_path, metavar="SOURCE", help="source file"
    )
    parser.add_argument(
        "-o", dest="output", type=_path, metavar="OUTPUT", help="output file"
    )
    return parser


def _parse(argv: list[str]) -> tuple[argparse.Namespace, dict[str, object]]:
    # Panel flags first: they use single-dash long spellings such as -opt and
    # -char that argparse would split into short-option clusters.  from_flags
    # consumes what it recognises and hands the rest back in order.
    built, rest = from_flags(argv)
    # argparse attaches operands only to single-character options; keep the
    # traditional attached form of -ir (-irDIR) by splitting it here.
    host_argv: list[str] = []
    for token in rest:
        if token.startswith("-ir") and len(token) > 3 and not token.startswith("-ir="):
            host_argv += ["-ir", token[3:]]
        else:
            host_argv.append(token)
    options, unknown = _parser().parse_known_args(host_argv)
    if unknown:
        raise ValueError(f"unknown argument: {unknown[0]}")
    if options.prefix is not None:
        built["C/C++ Compiler"].MWFrontEnd_C_prefixname = options.prefix.name
    if options.cw_root is None:
        options.cw_root = config.cw_root()
    return options, built


def _message_text(message) -> str:
    path = str(message.file.host) if message.file is not None else "<compiler>"
    label = "warning" if message.level == 1 else "error"
    head = f"{path}:{message.line}: {label}: {message.text} [{message.number}]"
    if message.source_line:
        return f"{head}\n{message.source_line}"
    return head


def _write_output(data: bytes, output: Path, *, preprocess: bool, vfs: VFS) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    node = vfs.node(output)
    node.write_data_fork(data)
    if not preprocess:
        node.set_type_creator(b"XCOF", b"MPS ")


def run(argv: list[str]) -> int:
    options, panel_objects = _parse(argv)
    if options.source is None or options.output is None:
        raise ValueError("cwcc requires -c source and -o output")
    if options.cw_root is None:
        raise ValueError("cwcc requires --cw-root or CWHOST_CW_ROOT")
    source = options.source.resolve()
    output = options.output.resolve()
    try:
        result = compile_unit(
            options.cw_root,
            source,
            CompileOptions(
                include_user=options.include_user,
                include_system=options.include_system,
                nostdinc=options.nostdinc,
                prefix=options.prefix,
                preprocess=options.preprocess,
                debug=options.debug,
                panels=panel_objects,
                trace=options.trace,
                trace_code=options.trace_code,
            ),
        )
        for message in result.messages:
            print(_message_text(message), file=sys.stderr)
        if result.status != cwNoErr:
            return 1
        _write_output(
            result.output, output, preprocess=options.preprocess, vfs=VFS(source.parent)
        )
    except HostError as error:
        print(f"cwcc: {error}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(list(sys.argv[1:] if argv is None else argv))
    except (UnimplementedImport, PluginError, HostError, ValueError, OSError) as error:
        print(f"cwcc: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
