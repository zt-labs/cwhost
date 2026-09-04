"""Host state and request protocol for a CodeWarrior plug-in session."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import abi, loader
from .errors import HostError, NotFound, PluginError
from .guest import Guest
from .memory import Handles, Heap
from .resources import ResourceChain
from .vfs import VFS, Node

REQ_INITIALIZE = -2
REQ_TERMINATE = -1
REQ_COMPILE = 0

# DropInCompilerLinker.h linkOutput* enum.
linkOutputNone = 0
linkOutputFile = 1
linkOutputDirectory = 2


@dataclass
class Harness:
    """A thin, plug-in-free shim session for focused Carbon tests."""

    vfs: VFS
    guest: Guest = field(default_factory=Guest)
    open_forks: dict[int, Any] = field(default_factory=dict)
    next_fork_ref: int = 1
    interrupted: bool = False

    def __post_init__(self) -> None:
        self.heap = Heap(self.guest)
        self.handles = Handles(self.heap)
        self.resources = ResourceChain(self.vfs)
        self.mem_error = 0
        self.fp_flags = 0


@dataclass
class TargetInfo:
    """The subset of DropInCompilerLinker.h CWTargetInfo used by M0."""

    output_type: int
    cpu: bytes = b"ppc "
    os: bytes = b"mac "
    outfile: Node | None = None
    creator: bytes = b"MPS "
    file_type: bytes = b"XCOF"
    name: str = "Target"


@dataclass
class SessionFlags:
    preprocess: bool = False
    debug: bool = False
    precompile: bool = False
    auto_precompile: bool = False
    cache_precompiled_headers: bool = False


@dataclass
class Message:
    level: int
    text: str
    file: Node | None
    line: int
    number: int
    source_line: str = ""


@dataclass
class ObjectData:
    bytes: bytes
    browse: bytes
    codesize: int
    udatasize: int
    idatasize: int
    dependencies: list[Any]


@dataclass
class _CWMemHandle:
    handle: int
    block: int
    size: int
    request_scoped: bool


class Session(Harness):
    """A PPC guest plus the PluginLib5 state for one loaded plug-in."""

    def __init__(
        self,
        plugin_path: Path,
        *,
        vfs: VFS,
        panels: dict[str, bytes],
        target: TargetInfo,
        files: list[Node],
        include_user: list[Node],
        include_system: list[Node],
        api_version: int = 13,
        flags: SessionFlags | None = None,
        trace_path: Path | None = None,
    ) -> None:
        super().__init__(vfs=vfs)
        self.plugin_path = Path(plugin_path)
        self.panels = {str(key): bytes(value) for key, value in panels.items()}
        self.target = target
        self.files = list(files)
        self.include_user = list(include_user)
        self.include_system = list(include_system)
        self.api_version = int(api_version)
        self.flags = flags if flags is not None else SessionFlags()
        self.trace_path = Path(trace_path) if trace_path is not None else None
        self.messages: list[Message] = []
        self.objects: dict[int, ObjectData] = {}
        self.texts: dict[str, bytes] = {}
        self.dropin_flags: dict[str, Any] = {}
        self.target_list: dict[str, Any] = {}
        self.mapping_list: dict[str, Any] = {}
        self.fragment: loader.Fragment | None = None
        self.ctx_ptr: int | None = None
        self.init_block: int | None = None
        self.plugin_name_ptr: int | None = None
        self.resource_ref: int | None = None
        self._current: int | None = None
        self._done: int | None = None
        self._done_count = 0
        self._state = "new"
        self._closed = False
        self._cw_handles: dict[int, _CWMemHandle] = {}
        self._text_buffers: dict[int, bytes] = {}
        self._secret_words: dict[str, int] = {}
        self._secret_buffers: dict[str, int] = {}
        # Opaque guest tokens returned by the private license callback.
        self._license_tokens: dict[int, int] = {}
        self._file_ids: dict[int, int] = {}
        self._last_loaded_node: Node | None = None
        self.message_type = Message
        self.object_type = ObjectData
        self.project_node = self.vfs.node(
            (
                self.target.outfile.host.parent
                if self.target.outfile
                else self.vfs.build_root
            )
            / "cwhost.mcp"
        )

    def _check_context(self, context: int) -> None:
        if self.ctx_ptr is None or context != self.ctx_ptr:
            raise PluginError(f"callback received invalid CWPluginContext {context:#x}")

    def _done_request(self, result: int) -> None:
        if self._done_count:
            raise PluginError(
                f"CWDonePluginRequest called more than once for request {self._current}"
            )
        self._done = int(result)
        self._done_count = 1

    def _alloc_cw_handle(self, data: bytes, request_scoped: bool = True) -> int:
        raw = bytes(data)
        handle = self.heap.alloc(8, align=4)
        block = self.heap.alloc(len(raw), align=4)
        if raw:
            self.guest.write(block, raw)
        self.guest.w32(handle, block)
        self.guest.w32(handle + 4, len(raw))
        self._cw_handles[handle] = _CWMemHandle(handle, block, len(raw), request_scoped)
        return handle

    def _cw_handle(self, handle: int) -> _CWMemHandle:
        try:
            item = self._cw_handles[handle]
        except KeyError as error:
            raise HostError(f"invalid CWMemHandle {handle:#x}") from error
        if (
            self.guest.u32(handle) != item.block
            or self.guest.u32(handle + 4) != item.size
        ):
            raise HostError(f"modified CWMemHandle {handle:#x}")
        return item

    def _cw_handle_block(self, handle: int) -> int:
        return self._cw_handle(handle).block

    def _cw_handle_size(self, handle: int) -> int:
        return self._cw_handle(handle).size

    def _cw_handle_bytes(self, handle: int) -> bytes:
        item = self._cw_handle(handle)
        return self.guest.read(item.block, item.size)

    def _free_cw_handle(self, handle: int) -> None:
        item = self._cw_handles.pop(handle, None)
        if item is None:
            raise HostError(f"invalid CWMemHandle {handle:#x}")
        self.heap.free(item.block)
        self.heap.free(item.handle)

    def _free_request_handles(self) -> None:
        for address, item in list(self._cw_handles.items()):
            if item.request_scoped:
                self._free_cw_handle(address)

    def _text_pointer(self, data: bytes) -> int:
        # PluginLib5 text pointers are length-delimited but the classic
        # compiler also probes the terminator while scanning diagnostics.
        # Keep the public length unchanged and provide one private NUL byte.
        pointer = self.guest.alloc(len(data) + 1, align=4)
        if data:
            self.guest.write(pointer, data)
        self.guest.w8(pointer + len(data), 0)
        self._text_buffers[pointer] = bytes(data)
        return pointer

    def _secret_pointer(self, name: str) -> int:
        if name not in self._secret_buffers:
            if name not in self.panels:
                raise HostError(f"unknown secret preferences {name!r}")
            block = self.guest.alloc(max(1, len(self.panels[name])), align=4)
            if self.panels[name]:
                self.guest.write(block, self.panels[name])
            word = self.guest.alloc(4, align=4)
            self.guest.w32(word, block)
            self._secret_buffers[name] = block
            self._secret_words[name] = word
        return self._secret_words[name]

    def _file_id(self, node: Node) -> int:
        if node.id not in self._file_ids:
            self._file_ids[node.id] = len(self._file_ids)
        return self._file_ids[node.id]

    def _include_search_roots(self, fullsearch: bool, dependent: int) -> list[Node]:
        roots: list[Node] = []
        if not fullsearch:
            # A dependent index of -1 is how the compiler denotes the file
            # whose text was loaded most recently (not the main source).  This
            # matters for nested includes such as os_enum.h from the prefix.
            if 0 <= dependent < len(self.files):
                including = self.files[dependent]
            elif self._last_loaded_node is not None:
                including = self._last_loaded_node
            else:
                including = self.files[0] if self.files else None
            if including is not None and including.parent is not None:
                roots.append(including.parent)
            # Angle-bracket includes reported by the plug-in still arrive
            # with fullsearch=false.  After the including directory, apply
            # the configured user/system roots in their documented order.
            roots.extend([*self.include_user, *self.include_system])
        else:
            # Include search paths are directory Nodes.  Accepting a file Node
            # as well keeps the callback useful for callers that pre-resolve
            # a path, while preserving user-before-system ordering.
            roots.extend([*self.include_user, *self.include_system])
        return roots

    def _node_is_under(self, node: Node, root: Node) -> bool:
        cursor: Node | None = node
        while cursor is not None:
            if cursor.id == root.id:
                return True
            cursor = cursor.parent
        return False

    def _walk_slash_include(self, filename: str, roots: list[Node]) -> Node | None:
        components = filename.replace("\\", "/").split("/")
        for root in roots:
            if not root.is_dir:
                continue
            current: Node | None = root
            escaped = False
            for component in components:
                if current is None:
                    break
                if component in ("", "."):
                    continue
                if component == "..":
                    parent = current.parent
                    if parent is None or not self._node_is_under(parent, root):
                        escaped = True
                        break
                    current = parent
                    continue
                if not current.is_dir:
                    current = None
                    break
                matches = [
                    child
                    for child in current.children_named(component)
                    if child.name.casefold() == component.casefold()
                ]
                if not matches:
                    current = None
                    break
                current = matches[0]
            if escaped or current is None or current.is_dir:
                continue
            return current
        return None

    def _find_file(
        self, filename: str, fullsearch: bool, dependent: int
    ) -> Node | None:
        if ":" in filename:
            try:
                origin = self.files[0] if self.files else None
                if origin is not None and not origin.is_dir:
                    origin = origin.parent
                return self.vfs.resolve_hfs(filename, origin)
            except NotFound:
                pass
        roots = self._include_search_roots(fullsearch, dependent)
        if "/" in filename:
            node = self._walk_slash_include(filename, roots)
            if node is not None:
                self._last_loaded_node = node
            return node
        for root in roots:
            matches = root.children_named(filename) if root.is_dir else [root]
            for node in matches:
                if not node.is_dir and node.name.casefold() == filename.casefold():
                    self._last_loaded_node = node
                    return node
        return None

    def _file_spec_dict(self, node: Node) -> dict[str, Any]:
        from .vfs import encode_fsref

        parent = node.parent or node
        raw = node.name.encode("utf-16-be")
        units = [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]
        if len(units) > 255:
            raise HostError(f"file name too long: {node.name!r}")
        return {
            "parentDirRef": {"hidden": encode_fsref(parent)},
            "filename": {"length": len(units), "unicode": units},
        }

    def _write_init_block(self, image_base: int, container_size: int) -> int:
        block = self.guest.alloc(36, align=4)
        name = b"MW C-C++ PPC"
        self.plugin_name_ptr = self.guest.alloc(len(name) + 1, align=1)
        self.guest.write(self.plugin_name_ptr, bytes([len(name)]) + name)
        self.guest.w32(block + 0, 1)
        self.guest.w32(block + 4, 1)
        self.guest.w32(block + 8, 1)
        self.guest.w32(block + 12, 0)
        self.guest.w32(block + 16, image_base)
        self.guest.w32(block + 20, container_size)
        self.guest.w8(block + 24, 0)
        self.guest.w8(block + 25, 0)
        self.guest.w16(block + 26, 0)
        self.guest.w32(block + 28, self.plugin_name_ptr)
        self.guest.w32(block + 32, 0)
        self.init_block = block
        return block

    def _decode_dropin_flags(self, pointer: int) -> dict[str, Any]:
        values = abi.DropInFlags.unpack(self.guest, pointer)
        return values

    def _decode_target_list(self, pointer: int) -> dict[str, Any]:
        version = self.guest.u16(pointer)
        cpu_count = self.guest.u16(pointer + 2)
        cpus_ptr = self.guest.u32(pointer + 4)
        os_count = self.guest.u16(pointer + 8)
        oss_ptr = self.guest.u32(pointer + 10)
        cpus = (
            [self.guest.read(cpus_ptr + i * 4, 4) for i in range(cpu_count)]
            if cpus_ptr
            else []
        )
        oss = (
            [self.guest.read(oss_ptr + i * 4, 4) for i in range(os_count)]
            if oss_ptr
            else []
        )
        return {
            "version": version,
            "cpus": cpus,
            "oss": oss,
            "cpuCount": cpu_count,
            "osCount": os_count,
        }

    def _decode_mapping_list(self, pointer: int) -> dict[str, Any]:
        version = self.guest.u16(pointer)
        count = self.guest.u16(pointer + 2)
        mappings: list[dict[str, Any]] = []
        for i in range(count):
            at = self.guest.u32(pointer + 4) + i * 72
            mappings.append(
                {
                    "type": self.guest.read(at, 4),
                    "extension": self.guest.read(at + 4, 32).split(b"\0", 1)[0],
                    "flags": self.guest.u32(at + 36),
                    "editlanguage": self.guest.read(at + 40, 32).split(b"\0", 1)[0],
                }
            )
        return {"version": version, "mappings": mappings, "nMappings": count}

    def load(self) -> Session:
        if self._state != "new":
            raise HostError(f"session cannot load in state {self._state}")
        from . import carbon, pluginlib

        if self.trace_path is not None:
            self.guest.enable_trace(self.trace_path)
        self.ctx_ptr = self.guest.alloc(64, align=4)
        for name, handler in {**carbon.TABLE, **pluginlib.TABLE}.items():
            slot = self.guest.alloc_trap_slot(name)
            self.guest.on_trap(
                name,
                lambda guest, h=handler: h(self, guest),
                inline=bool(getattr(handler, "_cwhost_inline_trap", False)),
            )
            # The slot address is the import descriptor's code pointer.
            if not hasattr(self, "_slots"):
                self._slots = {}
            self._slots[name] = slot
        self.fragment = loader.load(self.guest, self.plugin_path, self._slots)
        resource_node = self.vfs.node(self.plugin_path)
        resource = resource_node.resource_fork()
        if not resource:
            raise HostError(f"plug-in has no resource fork: {self.plugin_path}")
        self.resource_ref = self.resources.open(resource_node)
        self.resources.use(self.resource_ref)
        init_block = self._write_init_block(
            self.fragment.image_base, self.plugin_path.stat().st_size
        )
        if self.fragment.init_tvector is not None:
            result = self.guest.call(self.fragment.init_tvector, init_block)
            if result:
                raise PluginError(f"plug-in initialization failed with {result}")
        flags_ptr = self.guest.alloc(4, align=4)
        flags_size = self.guest.alloc(4, align=4)
        self.guest.w32(flags_ptr, 0)
        self.guest.w32(flags_size, 0)
        self.guest.call(
            self.fragment.tvector("CWPlugin_GetDropInFlags"), flags_ptr, flags_size
        )
        actual_size = self.guest.u32(flags_size)
        if actual_size != abi.DropInFlags.size:
            raise PluginError(
                f"unexpected DropInFlags size {actual_size}, expected {abi.DropInFlags.size}"
            )
        self.dropin_flags = self._decode_dropin_flags(self.guest.u32(flags_ptr))
        target_ptr = self.guest.alloc(4, align=4)
        self.guest.w32(target_ptr, 0)
        self.guest.call(self.fragment.tvector("CWPlugin_GetTargetList"), target_ptr)
        self.target_list = self._decode_target_list(self.guest.u32(target_ptr))
        mapping_ptr = self.guest.alloc(4, align=4)
        self.guest.w32(mapping_ptr, 0)
        self.guest.call(
            self.fragment.tvector("CWPlugin_GetDefaultMappingList"), mapping_ptr
        )
        self.mapping_list = self._decode_mapping_list(self.guest.u32(mapping_ptr))
        self._state = "loaded"
        return self

    def request(self, code: int) -> int:
        if self.fragment is None or self.ctx_ptr is None:
            raise HostError("session is not loaded")
        if self._state == "terminated":
            raise PluginError("request after termination")
        self._current = int(code)
        self._done = None
        self._done_count = 0
        result = self.guest.call(self.fragment.main_tvector, self.ctx_ptr)
        if self._done_count != 1:
            raise PluginError(
                f"request {code} returned without exactly one CWDonePluginRequest"
            )
        done = int(self._done) if self._done is not None else int(result)
        self._free_request_handles()
        if code == REQ_INITIALIZE:
            self._state = "initialized"
        elif code == REQ_TERMINATE:
            self._state = "terminated"
        self._current = None
        return done

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._state == "initialized":
                self.request(REQ_TERMINATE)
            if (
                self._state in ("initialized", "terminated")
                and self.fragment is not None
                and self.fragment.term_tvector is not None
            ):
                result = self.guest.call(self.fragment.term_tvector)
                if result:
                    raise PluginError(f"plug-in termination failed with {result}")
        finally:
            self.guest.close()
            self._closed = True
