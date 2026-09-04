"""PluginLib5 callback registry for the classic CodeWarrior plug-ins."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import abi
from .errors import HostError
from .vfs import decode_fsref, encode_fsref

TABLE: dict[str, Callable[[Any, Any], Any]] = {}

# CWPluginErrors.h result values.
cwNoErr = 0
cwErrUserCanceled = 1
cwErrRequestFailed = 2
cwErrInvalidParameter = 3
cwErrInvalidCallback = 4
cwErrOSError = 6
cwErrOutOfMemory = 7
cwErrFileNotFound = 8
cwErrSilent = 10


def shim(name: str):
    """Register a PluginLib5 callback (CWPlugins.h/DropInCompilerLinker.h)."""

    def decorate(function):
        if name in TABLE:
            raise ValueError(f"duplicate PluginLib5 shim {name}")
        TABLE[name] = function
        return function

    return decorate


def _ctx(session: Any, guest: Any, ptr: int) -> None:
    session._check_context(ptr)


def _result(guest: Any, value: int = 0) -> int:
    guest.ret(value)
    return value


def _cstr(guest: Any, ptr: int) -> bytes:
    if not ptr:
        return b""
    data = bytearray()
    for i in range(1 << 20):
        byte = guest.u8(ptr + i)
        if byte == 0:
            return bytes(data)
        data.append(byte)
    raise HostError(f"unterminated C string at {ptr:#x}")


def _write_cstr(guest: Any, ptr: int, max_length: int, value: bytes) -> None:
    if not ptr or max_length <= 0:
        return
    raw = bytes(value)[: max(0, max_length - 1)]
    guest.write(ptr, raw + b"\0")


def _spec(guest: Any, address: int, node: Any) -> None:
    # Keep the FSRef identity for the parent and use HFS+ UTF-16 for the name.
    parent = node.parent or node
    text = node.name
    units = list(text.encode("utf-16-be"))
    codepoints = [
        int.from_bytes(bytes(units[i : i + 2]), "big") for i in range(0, len(units), 2)
    ]
    if len(codepoints) > 255:
        raise HostError(f"CWFileSpec name too long: {text!r}")
    abi.CWFileSpec.pack(
        guest,
        address,
        parentDirRef={"hidden": encode_fsref(parent)},
        filename={
            "length": len(codepoints),
            "unicode": codepoints + [0] * (255 - len(codepoints)),
        },
    )


def _read_spec(session: Any, guest: Any, address: int) -> Any:
    values = abi.CWFileSpec.unpack(guest, address)
    parent = decode_fsref(values["parentDirRef"]["hidden"])
    length = values["filename"]["length"]
    raw = b"".join(
        int(x).to_bytes(2, "big") for x in values["filename"]["unicode"][:length]
    )
    try:
        name = raw.decode("utf-16-be")
    except UnicodeDecodeError as error:
        raise HostError("invalid CWFileSpec Unicode name") from error
    if not name:
        return parent
    for child in parent.children_named(name):
        if child.name.casefold() == name.casefold():
            return child
    return session.vfs.node(parent.host / name)


@shim("CWGetPluginRequest")
def CWGetPluginRequest(session: Any, guest: Any) -> int:
    context, request = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    if session._current is None:
        raise HostError("CWGetPluginRequest outside request")
    guest.w32(request, session._current)
    return _result(guest)


@shim("CWDonePluginRequest")
def CWDonePluginRequest(session: Any, guest: Any) -> int:
    context, result = guest.args(("ptr", "u32"))
    _ctx(session, guest, context)
    if result & 0x80000000:
        result -= 1 << 32
    session._done_request(result)
    return _result(guest)


@shim("CWGetAPIVersion")
def CWGetAPIVersion(session: Any, guest: Any) -> int:
    context, output = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    guest.w32(output, session.api_version)
    return _result(guest)


@shim("CWGetProjectFileCount")
def CWGetProjectFileCount(session: Any, guest: Any) -> int:
    context, output = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    guest.w32(output, len(session.files))
    return _result(guest)


@shim("CWGetProjectFile")
def CWGetProjectFile(session: Any, guest: Any) -> int:
    context, output = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    project = session.project_node
    _spec(guest, output, project)
    return _result(guest)


@shim("CWGetTargetName")
def CWGetTargetName(session: Any, guest: Any) -> int:
    context, output, maximum = guest.args(("ptr", "ptr", "s16"))
    _ctx(session, guest, context)
    _write_cstr(guest, output, maximum, session.target.name.encode("mac_roman"))
    return _result(guest)


@shim("CWGetMainFileNumber")
def CWGetMainFileNumber(session: Any, guest: Any) -> int:
    context, output = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    guest.w32(output, 0)
    return _result(guest)


@shim("CWGetMainFileID")
def CWGetMainFileID(session: Any, guest: Any) -> int:
    context, output = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    guest.w16(output, 0)
    return _result(guest)


@shim("CWGetMainFileSpec")
def CWGetMainFileSpec(session: Any, guest: Any) -> int:
    context, output = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    if session.files:
        _spec(guest, output, session.files[0])
    else:
        _spec(guest, output, session.project_node)
    return _result(guest)


@shim("CWGetMainFileText")
def CWGetMainFileText(session: Any, guest: Any) -> int:
    context, text_out, length_out = guest.args(("ptr", "ptr", "ptr"))
    _ctx(session, guest, context)
    data = session.files[0].text_fork() if session.files else b""
    ptr = session._text_pointer(data)
    guest.w32(text_out, ptr)
    guest.w32(length_out, len(data))
    return _result(guest)


@shim("CWGetTargetInfo")
def CWGetTargetInfo(session: Any, guest: Any) -> int:
    context, output = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    target = session.target
    empty = session.project_node
    abi.CWTargetInfo.pack(
        guest,
        output,
        outputType=target.output_type,
        outfile=_file_spec_values(target.outfile or empty),
        symfile=_file_spec_values(empty),
        runfile=_file_spec_values(empty),
        linkType=0,
        canRun=0,
        canDebug=0,
        targetCPU=target.cpu,
        targetOS=target.os,
        outfileCreator=target.creator,
        outfileType=target.file_type,
        debuggerCreator=b"\0\0\0\0",
        runHelperCreator=b"\0\0\0\0",
        linkAgainstFile=_file_spec_values(empty),
    )
    return _result(guest)


def _file_spec_values(node: Any) -> dict[str, Any]:
    from .vfs import encode_fsref

    parent = node.parent or node
    raw = node.name.encode("utf-16-be")
    units = [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]
    return {
        "parentDirRef": {"hidden": encode_fsref(parent)},
        "filename": {"length": len(units), "unicode": units + [0] * (255 - len(units))},
    }


@shim("CWGetBrowseOptions")
def CWGetBrowseOptions(session: Any, guest: Any) -> int:
    context, output = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    abi.CWBrowseOptions.pack(guest, output)
    return _result(guest)


def _bool_callback(name: str, attr: str):
    @shim(name)
    def callback(session: Any, guest: Any) -> int:
        context, output = guest.args(("ptr", "ptr"))
        _ctx(session, guest, context)
        guest.w8(output, int(bool(getattr(session.flags, attr))))
        return _result(guest)

    return callback


_bool_callback("CWIsPrecompiling", "precompile")
_bool_callback("CWIsAutoPrecompiling", "auto_precompile")
_bool_callback("CWIsPreprocessing", "preprocess")
_bool_callback("CWIsGeneratingDebugInfo", "debug")
_bool_callback("CWIsCachingPrecompiledHeaders", "cache_precompiled_headers")


@shim("CWGetNamedPreferences")
def CWGetNamedPreferences(session: Any, guest: Any) -> int:
    context, name_ptr, output = guest.args(("ptr", "ptr", "ptr"))
    _ctx(session, guest, context)
    name = _cstr(guest, name_ptr).decode("mac_roman")
    if name not in session.panels:
        raise HostError(f"unknown named preferences {name!r}")
    guest.w32(
        output, session._alloc_cw_handle(session.panels[name], request_scoped=True)
    )
    return _result(guest)


@shim("CWFreeMemHandle")
def CWFreeMemHandle(session: Any, guest: Any) -> int:
    context, handle = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    session._free_cw_handle(handle)
    return _result(guest)


@shim("CWLockMemHandle")
def CWLockMemHandle(session: Any, guest: Any) -> int:
    context, handle, _move_hi, output = guest.args(("ptr", "ptr", "u8", "ptr"))
    _ctx(session, guest, context)
    guest.w32(output, session._cw_handle_block(handle))
    return _result(guest)


@shim("CWUnlockMemHandle")
def CWUnlockMemHandle(session: Any, guest: Any) -> int:
    context, handle = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    session._cw_handle(handle)
    return _result(guest)


@shim("CWCheckoutLicense")
def CWCheckoutLicense(session: Any, guest: Any) -> int:
    # The private CWCheckoutLicense ABI is six arguments.  The final argument
    # is a pointer to the caller's opaque license-cookie slot; the compiler
    # tests that slot after the callback, so returning only cwNoErr is not a
    # usable license implementation.  Keep one stable, opaque guest token per
    # slot for the lifetime of this session.
    context, _license_name, _version, _kind, _flags, cookie_out = guest.args(
        ("ptr", "ptr", "ptr", "u32", "u32", "ptr")
    )
    _ctx(session, guest, context)
    if cookie_out:
        token = session._license_tokens.get(cookie_out)
        if token is None:
            token = session.guest.alloc(4, align=4)
            session.guest.w32(token, 0x4C494345)  # opaque ``LICE`` marker
            session._license_tokens[cookie_out] = token
        guest.w32(cookie_out, token)
    return _result(guest)


@shim("CWCheckinLicense")
def CWCheckinLicense(session: Any, guest: Any) -> int:
    context, _license = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    return _result(guest)


@shim("CWDisplayLines")
def CWDisplayLines(session: Any, guest: Any) -> int:
    context, _lines = guest.args(("ptr", "u32"))
    _ctx(session, guest, context)
    return _result(guest)


@shim("CWUserBreak")
def CWUserBreak(session: Any, guest: Any) -> int:
    context = guest.args(("ptr",))[0]
    _ctx(session, guest, context)
    return _result(guest, int(bool(session.interrupted)))


@shim("CWReleaseFileText")
def CWReleaseFileText(session: Any, guest: Any) -> int:
    context, ptr = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    session._text_buffers.pop(ptr, None)
    return _result(guest)


@shim("CWFindAndLoadFile")
def CWFindAndLoadFile(session: Any, guest: Any) -> int:
    context, filename_ptr, info_ptr = guest.args(("ptr", "ptr", "ptr"))
    _ctx(session, guest, context)
    filename = _cstr(guest, filename_ptr).decode("mac_roman")
    info = abi.CWFileInfo.unpack(guest, info_ptr)
    node = session._find_file(
        filename, bool(info["fullsearch"]), info["isdependentoffile"]
    )
    if node is None:
        return _result(guest, cwErrFileNotFound)
    data = node.text_fork()
    ptr = session._text_pointer(data)
    abi.CWFileInfo.write_field(guest, info_ptr, "filedata", ptr)
    abi.CWFileInfo.write_field(guest, info_ptr, "filedatalength", len(data))
    abi.CWFileInfo.write_field(guest, info_ptr, "filedatatype", 1)
    abi.CWFileInfo.write_field(guest, info_ptr, "fileID", session._file_id(node))
    abi.CWFileInfo.write_field(guest, info_ptr, "filespec", _file_spec_values(node))
    return _result(guest)


@shim("CWReportMessage")
def CWReportMessage(session: Any, guest: Any) -> int:
    context, msg_ptr, line1, line2, level, number = guest.args(
        ("ptr", "ptr", "ptr", "ptr", "s16", "u32")
    )
    _ctx(session, guest, context)
    if number & 0x80000000:
        number -= 1 << 32
    ref = abi.CWMessageRef.unpack(guest, msg_ptr) if msg_ptr else None
    file_node = None
    line = 0
    if ref:
        line = ref["linenumber"]
        try:
            file_node = _read_spec(session, guest, msg_ptr)
        except HostError:
            file_node = None
    left = _cstr(guest, line1).decode("mac_roman", "replace") if line1 else ""
    right = _cstr(guest, line2).decode("mac_roman", "replace") if line2 else ""
    session.messages.append(
        session.message_type(level, left, file_node, line, number, source_line=right)
    )
    return _result(guest)


@shim("CWCreateNewTextDocument")
def CWCreateNewTextDocument(session: Any, guest: Any) -> int:
    context, doc_ptr = guest.args(("ptr", "ptr"))
    _ctx(session, guest, context)
    doc = abi.CWNewTextDocumentInfo.unpack(guest, doc_ptr)
    name = (
        _cstr(guest, doc["documentname"]).decode("mac_roman")
        if doc["documentname"]
        else "untitled"
    )
    text_ptr = doc["text"]
    if text_ptr in session._cw_handles:
        data = session._cw_handle_bytes(text_ptr)
    elif text_ptr:
        # The compiler's preprocess document callback passes a direct
        # NUL-terminated text pointer despite the SDK's CWMemHandle typedef.
        data = _cstr(guest, text_ptr)
    else:
        data = b""
    session.texts[name] = bytes(data)
    return _result(guest)


@shim("CWStoreObjectData")
def CWStoreObjectData(session: Any, guest: Any) -> int:
    context, which, object_ptr = guest.args(("ptr", "u32", "ptr"))
    _ctx(session, guest, context)
    if which & 0x80000000:
        which -= 1 << 32
    obj = abi.CWObjectData.unpack(guest, object_ptr)

    def read_attached(value: int) -> bytes:
        if not value:
            return b""
        # CWSecretAttachHandle returns a locked pointer to the block owned by
        # the classic Memory Manager handle.  Its allocation size is the
        # object size; codesize/udatasize/idatasize describe sections inside
        # the MWOB and do not account for its header and name table.
        if value in session._cw_handles:
            return session._cw_handle_bytes(value)
        try:
            size = session.handles.block_size(value)
        except HostError as error:
            raise HostError(f"invalid attached memory block {value:#x}") from error
        return guest.read(value, size)

    raw = read_attached(obj["objectdata"])
    browse = read_attached(obj["browsedata"])
    deps = []
    if obj["dependencies"] and obj["dependencyCount"] > 0:
        for i in range(obj["dependencyCount"]):
            deps.append(
                abi.CWDependencyInfo.unpack(
                    guest, obj["dependencies"] + i * abi.CWDependencyInfo.size
                )
            )
    session.objects[which] = session.object_type(
        raw, browse, obj["codesize"], obj["udatasize"], obj["idatasize"], deps
    )
    return _result(guest)


@shim("CWSecretGetNamedPreferences")
def CWSecretGetNamedPreferences(session: Any, guest: Any) -> int:
    context, name_ptr, output = guest.args(("ptr", "ptr", "ptr"))
    _ctx(session, guest, context)
    name = _cstr(guest, name_ptr).decode("mac_roman")
    pointer = session._secret_pointer(name)
    guest.w32(output, pointer)
    return _result(guest)


@shim("CWSecretAttachHandle")
def CWSecretAttachHandle(session: Any, guest: Any) -> int:
    context, value, output = guest.args(("ptr", "ptr", "ptr"))
    _ctx(session, guest, context)
    if not value:
        if output:
            guest.w32(output, 0)
        return _result(guest)
    # The private callback receives a classic Memory Manager Handle.  The
    # compiler's wrapper immediately treats the returned value as the locked
    # data pointer and forgets the original master pointer, so expose the
    # current block while retaining the host-side allocation for StoreObjectData.
    block = session.handles.block(value)
    if output:
        guest.w32(output, block)
    return _result(guest)


STUBS = (
    "CWAllocMemHandle",
    "CWCachePrecompiledHeader",
    "CWGetFileText",
    "CWGetPrecompiledHeaderSpec",
    "CWSetModDate",
    "CWShowStatus",
)


def _register_stubs() -> None:
    from .errors import UnimplementedImport

    for name in STUBS:
        if name in TABLE:
            raise ValueError(f"cannot stub already-registered PluginLib5 shim {name}")

        def handler(session: Any, guest: Any, _name: str = name) -> None:
            raise UnimplementedImport(_name)

        shim(name)(handler)


_register_stubs()
