"""Virtual Build:/Host: volumes and classic Mac file identities."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

try:
    import xattr as _xattr
except ImportError:  # pragma: no cover - dependency is declared by the project
    _xattr = None

from . import config
from .appledouble import AppleDouble
from .errors import HostError, NotFound

MAC_EPOCH = 2_082_844_800

# CodeWarrior asks the host for these files as source text.  Data/resource
# forks remain byte-for-byte untouched; only the compiler text callback maps
# LF-only source to the classic Mac CR convention.
_TEXT_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cp",
        ".cpp",
        ".cxx",
        ".c++",
        ".h",
        ".hh",
        ".hp",
        ".hpp",
        ".hxx",
        ".h++",
        ".pch",
        ".pch++",
        ".inc",
        ".inl",
        ".ipp",
        ".i",
        ".ii",
        ".m",
        ".mm",
    }
)

_NODE_BY_ID: dict[int, Node] = {}
_UNSET = object()


def _native_xattr(path: Path, name: str) -> bytes | None:
    getter = getattr(os, "getxattr", None) or getattr(_xattr, "getxattr", None)
    if getter is not None:
        try:
            return bytes(getter(path, name))
        except OSError:
            return None
    if not shutil_which("xattr"):
        return None
    result = subprocess.run(
        ["xattr", "-p", "-x", name, str(path)], capture_output=True, check=False
    )
    if result.returncode:
        return None
    try:
        return bytes.fromhex(result.stdout.decode().strip())
    except ValueError:
        return None


def _native_setxattr(path: Path, name: str, value: bytes) -> bool:
    setter = getattr(os, "setxattr", None) or getattr(_xattr, "setxattr", None)
    if setter is not None:
        try:
            setter(path, name, value)
        except OSError:
            pass
        else:
            return True
    if not shutil_which("xattr"):
        return False
    result = subprocess.run(
        ["xattr", "-w", "-x", name, value.hex(), str(path)],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def shutil_which(command: str) -> str | None:
    # Keep the module dependency-free while still supporting Python builds
    # whose os module lacks the xattr calls on macOS.
    import shutil

    return shutil.which(command)


class Node:
    def __init__(self, vfs: VFS, host: Path, volume: str, node_id: int):
        self.vfs = vfs
        self.host = host
        self.volume = volume
        self.id = node_id
        self._parent: Node | object | None = _UNSET

    @property
    def name(self) -> str:
        root = self.vfs._roots[self.volume]
        return "" if self.host == root else self.host.name

    @property
    def parent(self) -> Node | None:
        if self._parent is _UNSET:
            root = self.vfs._roots[self.volume]
            self._parent = (
                None if self.host == root else self.vfs.node(self.host.parent)
            )
        assert self._parent is None or isinstance(self._parent, Node)
        return self._parent

    @property
    def is_dir(self) -> bool:
        return self.host.is_dir()

    @property
    def exists(self) -> bool:
        return self.host.exists()

    def data_fork(self) -> bytes:
        try:
            return self.host.read_bytes()
        except OSError as error:
            raise HostError(f"cannot read data fork {self.host}: {error}") from error

    def text_fork(self) -> bytes:
        """Return compiler text with LF-only sources mapped to classic Mac CR."""
        data = self.data_fork()
        if (
            self.host.suffix.casefold() in _TEXT_SUFFIXES
            and b"\0" not in data
            and b"\n" in data
            and b"\r" not in data
        ):
            return data.replace(b"\n", b"\r")
        return data

    def write_data_fork(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)):
            raise HostError("data fork must be bytes")
        try:
            self.host.parent.mkdir(parents=True, exist_ok=True)
            self.host.write_bytes(bytes(data))
        except OSError as error:
            raise HostError(f"cannot write data fork {self.host}: {error}") from error
        self.vfs.invalidate_path(self.host)

    def delete(self) -> None:
        try:
            if self.is_dir:
                self.host.rmdir()
            else:
                self.host.unlink()
        except OSError as error:
            raise HostError(f"cannot delete {self.host}: {error}") from error
        self.vfs.invalidate_path(self.host)

    def rename(self, destination: Path | Node) -> Node:
        destination_host = (
            destination.host if isinstance(destination, Node) else Path(destination)
        )
        destination_host = destination_host.expanduser().resolve()
        old_host = self.host
        try:
            old_host.rename(destination_host)
        except OSError as error:
            raise HostError(
                f"cannot rename {old_host} to {destination_host}: {error}"
            ) from error
        self.vfs.invalidate_path(old_host)
        self.vfs.invalidate_path(destination_host)
        return self.vfs.node(destination_host)

    @property
    def _sidecar(self) -> Path:
        return self.host.parent / ("._" + self.host.name)

    def _read_sidecar_entry(self, entry_id: int) -> bytes | None:
        if not self._sidecar.exists():
            return None
        return AppleDouble.read(self._sidecar).entries.get(entry_id)

    def resource_fork(self) -> bytes:
        native = _native_xattr(self.host, "com.apple.ResourceFork")
        if native is not None:
            return native
        namedfork = self.host / "..namedfork" / "rsrc"
        try:
            if namedfork.exists():
                return namedfork.read_bytes()
        except OSError:
            pass
        return self._read_sidecar_entry(2) or b""

    def _write_sidecar_entry(self, entry_id: int, data: bytes) -> None:
        ad = (
            AppleDouble.read(self._sidecar)
            if self._sidecar.exists()
            else AppleDouble.new()
        )
        ad.entries[entry_id] = bytes(data)
        ad.write(self._sidecar)

    def write_resource_fork(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)):
            raise HostError("resource fork must be bytes")
        raw = bytes(data)
        if _native_setxattr(self.host, "com.apple.ResourceFork", raw):
            self.vfs.invalidate_path(self.host)
            return
        namedfork = self.host / "..namedfork" / "rsrc"
        try:
            if namedfork.exists() or self.host.exists():
                namedfork.write_bytes(raw)
                self.vfs.invalidate_path(self.host)
                return
        except OSError:
            pass
        self._write_sidecar_entry(2, raw)
        self.vfs.invalidate_path(self.host)

    def finder_info(self) -> bytes:
        native = _native_xattr(self.host, "com.apple.FinderInfo")
        if native is None:
            native = self._read_sidecar_entry(9)
        raw = native or b""
        return (raw + b"\0" * 32)[:32]

    def set_finder_info(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)) or len(data) != 32:
            raise HostError("FinderInfo must be exactly 32 bytes")
        raw = bytes(data)
        if _native_setxattr(self.host, "com.apple.FinderInfo", raw):
            self.vfs.invalidate_path(self.host)
            return
        self._write_sidecar_entry(9, raw)
        self.vfs.invalidate_path(self.host)

    def type_creator(self) -> tuple[bytes, bytes]:
        info = self.finder_info()
        return info[:4], info[4:8]

    def set_type_creator(self, file_type: bytes, creator: bytes) -> None:
        if isinstance(file_type, str):
            file_type = file_type.encode("mac_roman")
        if isinstance(creator, str):
            creator = creator.encode("mac_roman")
        if len(file_type) != 4 or len(creator) != 4:
            raise HostError("FinderInfo type and creator must be four bytes each")
        info = bytearray(self.finder_info())
        info[:4] = file_type
        info[4:8] = creator
        self.set_finder_info(bytes(info))

    def mod_time(self) -> int:
        fixed = config.fixed_time()
        if fixed is not None:
            return fixed[0] + MAC_EPOCH
        try:
            return int(self.host.stat().st_mtime) + MAC_EPOCH
        except OSError as error:
            raise HostError(f"cannot stat {self.host}: {error}") from error

    def children(self) -> list[Node]:
        key = (self.volume, self.host)
        cached = self.vfs._children_cache.get(key)
        if cached is not None:
            return list(cached)
        if not self.is_dir:
            self.vfs._children_cache[key] = ()
            self.vfs._children_by_name_cache[key] = {}
            return []
        try:
            paths = sorted(
                self.host.iterdir(), key=lambda path: (path.name.casefold(), path.name)
            )
        except OSError as error:
            raise HostError(f"cannot list {self.host}: {error}") from error
        cached = tuple(self.vfs.node(path) for path in paths)
        by_name: dict[str, list[Node]] = {}
        for child in cached:
            by_name.setdefault(child.name.casefold(), []).append(child)
        self.vfs._children_cache[key] = cached
        self.vfs._children_by_name_cache[key] = {
            name: tuple(matches) for name, matches in by_name.items()
        }
        return list(cached)

    def children_named(self, name: str) -> list[Node]:
        """Return case-insensitive child matches in deterministic listing order."""
        key = (self.volume, self.host)
        if key not in self.vfs._children_cache:
            self.children()
        return list(self.vfs._children_by_name_cache[key].get(name.casefold(), ()))

    def __repr__(self) -> str:
        return f"Node({hfs_path(self)!r}, id={self.id})"


class VFS:
    def __init__(self, build_root: Path):
        self.build_root = Path(build_root).expanduser().resolve()
        self._roots = {"Build": self.build_root, "Host": Path("/")}
        self._volumes = {name.casefold(): name for name in self._roots}
        self._nodes: dict[tuple[str, Path], Node] = {}
        self._ids: dict[int, Node] = {}
        self._children_cache: dict[tuple[str, Path], tuple[Node, ...]] = {}
        self._children_by_name_cache: dict[
            tuple[str, Path], dict[str, tuple[Node, ...]]
        ] = {}
        self._next_id = 2
        self.node(self.build_root)  # Build volume root is the classic dirID 2.

    def _volume_for(self, host: Path) -> str:
        """Return the volume for an already canonical host path."""
        try:
            host.relative_to(self.build_root)
        except ValueError:
            return "Host"
        return "Build"

    def node(self, host_path: Path) -> Node:
        host = Path(host_path).expanduser().resolve()
        volume = self._volume_for(host)
        root = self._roots[volume]
        if volume == "Build":
            try:
                host.relative_to(root)
            except ValueError:
                volume = "Host"
                root = self._roots[volume]
        key = (volume, host)
        if key in self._nodes:
            return self._nodes[key]
        node = Node(self, host, volume, self._next_id)
        self._next_id += 1
        self._nodes[key] = node
        self._ids[node.id] = node
        _NODE_BY_ID[node.id] = node
        return node

    def invalidate_path(self, host_path: Path) -> None:
        """Drop cached listings affected by a host-side mutation.

        Clearing every cached ancestor handles both a file mutation and the
        intermediate directories that ``write_data_fork`` may create.
        The cache belongs to this VFS instance; it is never shared globally.
        """
        host = Path(host_path).expanduser().resolve()
        volume = self._volume_for(host)
        root = self._roots[volume]
        current = host
        while True:
            key = (volume, current)
            self._children_cache.pop(key, None)
            self._children_by_name_cache.pop(key, None)
            if current == root or current.parent == current:
                break
            current = current.parent

    def _root(self, volume: str) -> Node:
        try:
            return self.node(self._roots[volume])
        except KeyError as error:
            raise HostError(f"unknown volume {volume}") from error

    def resolve_hfs(self, path: str, cwd: Node | None) -> Node:
        if not isinstance(path, str) or not path:
            raise HostError("empty HFS path")
        leading = len(path) - len(path.lstrip(":"))
        if leading:
            if cwd is None:
                raise HostError("relative HFS path has no current directory")
            current = cwd
            for _ in range(leading - 1):
                parent = current.parent
                if parent is None:
                    raise HostError("HFS path traverses above volume root")
                current = parent
            components = [part for part in path[leading:].split(":") if part]
        else:
            parts = path.split(":")
            volume = self._volumes.get(parts.pop(0).casefold())
            if volume is None:
                raise HostError(f"unknown HFS volume in {path!r}")
            current = self._root(volume)
            components = [part for part in parts if part]
        for component in components:
            if component in (".", ""):
                continue
            if component == "..":
                parent = current.parent
                if parent is None:
                    raise HostError("HFS path traverses above volume root")
                current = parent
                continue
            matches = current.children_named(component)
            if not matches:
                raise NotFound(f"HFS component not found: {component!r}")
            current = matches[0]
        return current

    def fsspec(self, node: Node) -> tuple[int, int, bytes]:
        if node.vfs is not self:
            raise HostError("FSSpec node belongs to another VFS")
        volume_index = 0 if node.volume == "Build" else 1
        parent = node.parent
        parent_id = 2 if parent is None else parent.id
        try:
            name = node.name.encode("mac_roman")
        except UnicodeEncodeError as error:
            raise HostError(
                f"HFS name is not representable in MacRoman: {node.name!r}"
            ) from error
        if len(name) > 63:
            raise HostError("FSSpec name exceeds 63 bytes")
        return -(volume_index + 1), parent_id, bytes([len(name)]) + name

    def node_from_fsspec(self, vref_num: int, par_id: int, name: bytes | str) -> Node:
        volume_index = -int(vref_num) - 1
        if volume_index not in (0, 1):
            raise HostError(f"unknown FSSpec volume reference {vref_num}")
        volume = ("Build", "Host")[volume_index]
        parent = self._ids.get(int(par_id))
        if parent is None or parent.volume != volume:
            parent = self._root(volume) if int(par_id) == 2 else None
        if parent is None:
            raise HostError(f"unknown FSSpec parent directory {par_id}")
        raw = name.encode("mac_roman") if isinstance(name, str) else bytes(name)
        if raw and raw[0] == len(raw) - 1:
            raw = raw[1:]
        try:
            text = raw.decode("mac_roman")
        except UnicodeDecodeError as error:
            raise HostError("FSSpec name is not valid MacRoman") from error
        if not text:
            return parent
        current = parent
        for component in text.split(":"):
            if not component:
                continue
            matches = current.children_named(component)
            if not matches:
                raise HostError(f"FSSpec component not found: {component!r}")
            current = matches[0]
        return current


def hfs_path(node: Node) -> str:
    current: list[str] = []
    cursor: Node | None = node
    while cursor is not None and cursor.parent is not None:
        current.append(cursor.name)
        cursor = cursor.parent
    if cursor is None:
        raise HostError("node has no volume root")
    return cursor.volume + ":" + ":".join(reversed(current))


def encode_fsref(node: Node) -> bytes:
    return int(node.id).to_bytes(4, "big") + b"\0" * 76


def decode_fsref(data: bytes) -> Node:
    if len(data) != 80:
        raise HostError(f"FSRef must be 80 bytes, got {len(data)}")
    node_id = int.from_bytes(data[:4], "big")
    try:
        return _NODE_BY_ID[node_id]
    except KeyError as error:
        raise HostError(f"unknown FSRef node id {node_id}") from error


def write_fsref(guest: Any, addr: int, node: Node) -> None:
    guest.write(addr, encode_fsref(node))


def read_fsref(guest: Any, addr: int) -> Node:
    return decode_fsref(guest.read(addr, 80))


def fsspec(node: Node) -> tuple[int, int, bytes]:
    return node.vfs.fsspec(node)
