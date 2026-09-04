"""Read-only Macintosh Resource Manager parsing and open-file chains."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import HostError

if TYPE_CHECKING:
    from .vfs import VFS, Node


def _type_bytes(value: bytes | str) -> bytes:
    if isinstance(value, str):
        try:
            value = value.encode("mac_roman")
        except UnicodeEncodeError as error:
            raise HostError(f"resource type is not MacRoman: {value!r}") from error
    raw = bytes(value)
    if len(raw) != 4:
        raise HostError(f"resource type must be four bytes: {raw!r}")
    return raw


@dataclass(frozen=True)
class _ResourceRef:
    resource_id: int
    data_offset: int


class ResourceFork:
    def __init__(self, data: bytes, resources: dict[bytes, dict[int, bytes]]):
        self.data = bytes(data)
        self._resources = resources

    @classmethod
    def parse(cls, data: bytes) -> ResourceFork:
        raw = bytes(data)
        if len(raw) < 16:
            raise HostError("truncated resource fork header")
        data_offset, map_offset, data_length, map_length = struct.unpack_from(
            ">IIII", raw, 0
        )
        if data_offset + data_length > len(raw) or map_offset + map_length > len(raw):
            raise HostError("resource fork offsets exceed file length")
        if map_length < 30:
            raise HostError("truncated resource fork map")
        map_start = map_offset
        type_list_offset = struct.unpack_from(">H", raw, map_start + 24)[0]
        name_list_offset = struct.unpack_from(">H", raw, map_start + 26)[0]
        type_start = map_start + type_list_offset
        name_start = map_start + name_list_offset
        map_end = map_offset + map_length
        if type_start + 2 > map_end or name_start > map_end:
            raise HostError("invalid resource fork list offsets")
        type_count_minus_one = struct.unpack_from(">H", raw, type_start)[0]
        type_count = type_count_minus_one + 1
        type_entries_end = type_start + 2 + 8 * type_count
        if type_entries_end > map_end:
            raise HostError("truncated resource fork type list")
        resources: dict[bytes, dict[int, bytes]] = {}
        for index in range(type_count):
            entry = type_start + 2 + 8 * index
            resource_type = bytes(raw[entry : entry + 4])
            count = struct.unpack_from(">H", raw, entry + 4)[0] + 1
            ref_offset = struct.unpack_from(">H", raw, entry + 6)[0]
            refs_start = type_start + ref_offset
            refs_end = refs_start + 12 * count
            if refs_start < type_start or refs_end > map_end:
                raise HostError(
                    f"invalid reference list for resource type {resource_type!r}"
                )
            typed = resources.setdefault(resource_type, {})
            for ref_index in range(count):
                ref = refs_start + ref_index * 12
                resource_id = struct.unpack_from(">h", raw, ref)[0]
                data_delta = int.from_bytes(raw[ref + 5 : ref + 8], "big")
                data_position = data_offset + data_delta
                if data_position + 4 > data_offset + data_length:
                    raise HostError(
                        f"resource {resource_type!r}/{resource_id} data offset invalid"
                    )
                length = struct.unpack_from(">I", raw, data_position)[0]
                payload_start = data_position + 4
                payload_end = payload_start + length
                if payload_end > data_offset + data_length:
                    raise HostError(
                        f"resource {resource_type!r}/{resource_id} data truncated"
                    )
                if resource_id in typed:
                    raise HostError(
                        f"duplicate resource {resource_type!r}/{resource_id}"
                    )
                typed[resource_id] = bytes(raw[payload_start:payload_end])
        return cls(raw, resources)

    def types(self) -> list[bytes]:
        return list(self._resources)

    def ids(self, resource_type: bytes | str) -> list[int]:
        return list(self._resources.get(_type_bytes(resource_type), {}))

    def get(self, resource_type: bytes | str, resource_id: int) -> bytes | None:
        return self._resources.get(_type_bytes(resource_type), {}).get(int(resource_id))

    def str_list(self, resource_id: int) -> list[bytes]:
        payload = self.get(b"STR#", resource_id)
        if payload is None:
            raise HostError(f"missing STR# resource {resource_id}")
        if len(payload) < 2:
            raise HostError(f"truncated STR# resource {resource_id}")
        count = struct.unpack_from(">H", payload, 0)[0]
        strings: list[bytes] = []
        position = 2
        for index in range(count):
            if position >= len(payload):
                raise HostError(f"truncated STR# {resource_id} at string {index + 1}")
            length = payload[position]
            position += 1
            if position + length > len(payload):
                raise HostError(f"truncated STR# {resource_id} at string {index + 1}")
            strings.append(bytes(payload[position : position + length]))
            position += length
        return strings


class ResourceChain:
    def __init__(self, vfs: VFS):
        self.vfs = vfs
        self._files: dict[int, ResourceFork] = {}
        self._nodes: dict[int, Node] = {}
        self._order: list[int] = []
        self.current: int | None = None

    def open(self, node: Node) -> int:
        if node.vfs is not self.vfs:
            raise HostError("resource node belongs to another VFS")
        refnum = 1
        while refnum in self._files:
            refnum += 1
        self._files[refnum] = ResourceFork.parse(node.resource_fork())
        self._nodes[refnum] = node
        self._order.append(refnum)
        self.current = refnum
        return refnum

    def use(self, refnum: int) -> None:
        self._check(refnum)
        self.current = refnum

    def close(self, refnum: int) -> None:
        self._check(refnum)
        self._files.pop(refnum)
        self._nodes.pop(refnum, None)
        self._order.remove(refnum)
        self.current = self._order[-1] if self._order else None

    def _check(self, refnum: int) -> ResourceFork:
        try:
            return self._files[refnum]
        except KeyError as error:
            raise HostError(f"unknown resource file {refnum}") from error

    def get1(self, resource_type: bytes | str, resource_id: int) -> bytes | None:
        if self.current is None:
            return None
        return self._check(self.current).get(resource_type, resource_id)

    def get(self, resource_type: bytes | str, resource_id: int) -> bytes | None:
        if self.current is None:
            return None
        current_index = self._order.index(self.current)
        for refnum in reversed(self._order[: current_index + 1]):
            payload = self._check(refnum).get(resource_type, resource_id)
            if payload is not None:
                return payload
        return None

    def get_ind_string(self, list_id: int, index: int) -> bytes:
        if index < 1:
            raise HostError(f"STR# index is 1-based, got {index}")
        if self.current is None:
            raise HostError("no current resource file")
        current_index = self._order.index(self.current)
        for refnum in reversed(self._order[: current_index + 1]):
            strings = (
                self._check(refnum).str_list(list_id)
                if self._check(refnum).get(b"STR#", list_id) is not None
                else None
            )
            if strings is not None:
                if index > len(strings):
                    raise HostError(f"STR# {list_id} has no string {index}")
                return strings[index - 1]
        raise HostError(f"missing STR# resource {list_id}")
