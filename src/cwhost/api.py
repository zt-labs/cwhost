"""Library entry point: compile one translation unit with a CodeWarrior plug-in."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config, panels
from .errors import HostError
from .pluginlib import cwNoErr
from .session import (
    REQ_COMPILE,
    REQ_INITIALIZE,
    Message,
    Session,
    SessionFlags,
    TargetInfo,
)
from .vfs import VFS


@dataclass
class CompileOptions:
    include_user: list[Path]
    include_system: list[Path]
    nostdinc: bool = False
    prefix: Path | None = None
    preprocess: bool = False
    debug: bool = False
    panels: dict[str, Any] | None = None
    trace: Path | None = None
    trace_code: Path | None = None


@dataclass
class CompileResult:
    status: int
    messages: list[Message]
    output: bytes


def compile(cw_root: Path, source: Path, options: CompileOptions) -> CompileResult:
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise HostError(f"source file not found: {source}")
    toolchain = config.resolve(cw_root, nostdinc=options.nostdinc)
    vfs = VFS(source.parent)
    source_node = vfs.node(source)
    # The plug-in asks for an output file spec (CWGetTargetInfo) and an output
    # directory (CWGetOutputFileDirectory) but never writes there: the object
    # arrives through CWStoreObjectData and is returned in memory.  A spec
    # beside the source satisfies it and does not influence the object bytes.
    output_node = vfs.node(source.with_suffix(".o"))
    user_paths = [*list(options.include_user or []), source.parent]
    if options.prefix is not None and options.prefix.name:
        parent = options.prefix.expanduser().resolve().parent
        if parent not in user_paths:
            user_paths.append(parent)
    system_paths = [*list(options.include_system or []), *toolchain.include_system]
    user_nodes = [vfs.node(path.resolve()) for path in user_paths if path.exists()]
    system_nodes = [vfs.node(path.resolve()) for path in system_paths]
    panel_objects = options.panels if options.panels is not None else panels.defaults()
    panel_objects = {name: copy.copy(panel) for name, panel in panel_objects.items()}
    if options.prefix is not None:
        panel_objects["C/C++ Compiler"].MWFrontEnd_C_prefixname = options.prefix.name
    panels_bytes = {name: panel.to_bytes() for name, panel in panel_objects.items()}
    session = Session(
        toolchain.plugin,
        vfs=vfs,
        panels=panels_bytes,
        target=TargetInfo(output_type=1, outfile=output_node, name="Target"),
        files=[source_node],
        include_user=user_nodes,
        include_system=system_nodes,
        flags=SessionFlags(preprocess=options.preprocess, debug=options.debug),
        trace_path=options.trace,
    )
    try:
        session.load()
        if options.trace_code is not None:
            options.trace_code.parent.mkdir(parents=True, exist_ok=True)
            session.guest.enable_block_trace(options.trace_code)
        init = session.request(REQ_INITIALIZE)
        if init != cwNoErr:
            return CompileResult(
                status=init, messages=list(session.messages), output=b""
            )
        status = session.request(REQ_COMPILE)
        if options.preprocess:
            data = next(reversed(session.texts.values()), b"")
        else:
            stored = next(reversed(session.objects.values()), None)
            data = stored.bytes if stored is not None else b""
        if status == cwNoErr and not data:
            raise HostError(
                "compiler produced no preprocessed text"
                if options.preprocess
                else "compiler produced no object data"
            )
        return CompileResult(
            status=status, messages=list(session.messages), output=data
        )
    finally:
        session.close()
