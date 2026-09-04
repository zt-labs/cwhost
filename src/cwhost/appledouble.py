"""AppleDouble containers used for non-native Mac forks and Finder data."""

from __future__ import annotations

import struct
from pathlib import Path

from .errors import HostError

_MAGIC = 0x00051607
_VERSION = 0x00020000
_HEADER_SIZE = 26


class AppleDouble:
    def __init__(self, entries: dict[int, bytes] | None = None):
        self.entries: dict[int, bytes] = dict(entries or {})

    @classmethod
    def new(cls) -> AppleDouble:
        return cls()

    @classmethod
    def read(cls, path: Path) -> AppleDouble:
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise HostError(f"cannot read AppleDouble file {path}: {error}") from error
        if len(data) < _HEADER_SIZE:
            raise HostError(f"truncated AppleDouble header: {path}")
        magic, version = struct.unpack_from(">II", data, 0)
        if magic != _MAGIC or version != _VERSION:
            raise HostError(f"invalid AppleDouble header: {path}")
        count = struct.unpack_from(">H", data, 24)[0]
        table_end = _HEADER_SIZE + 12 * count
        if table_end > len(data):
            raise HostError(f"truncated AppleDouble entry table: {path}")
        entries: dict[int, bytes] = {}
        for index in range(count):
            entry_id, offset, length = struct.unpack_from(
                ">III", data, _HEADER_SIZE + 12 * index
            )
            if offset < table_end or offset + length > len(data):
                raise HostError(f"invalid AppleDouble entry {entry_id} in {path}")
            if entry_id in entries:
                raise HostError(f"duplicate AppleDouble entry {entry_id} in {path}")
            entries[entry_id] = bytes(data[offset : offset + length])
        return cls(entries)

    def write(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        count = len(self.entries)
        table_end = _HEADER_SIZE + 12 * count
        output = bytearray(struct.pack(">II", _MAGIC, _VERSION))
        output.extend(b"\0" * 16)
        output.extend(struct.pack(">H", count))
        offset = table_end
        payload = bytearray()
        for entry_id, value in self.entries.items():
            if not isinstance(entry_id, int) or not 0 <= entry_id <= 0xFFFF_FFFF:
                raise HostError(f"invalid AppleDouble entry id {entry_id!r}")
            if not isinstance(value, (bytes, bytearray)):
                raise HostError(f"AppleDouble entry {entry_id} is not bytes")
            raw = bytes(value)
            output.extend(struct.pack(">III", entry_id, offset, len(raw)))
            payload.extend(raw)
            offset += len(raw)
        output.extend(payload)
        try:
            path.write_bytes(output)
        except OSError as error:
            raise HostError(f"cannot write AppleDouble file {path}: {error}") from error
