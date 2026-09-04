"""PEF (Preferred Executable Format) container parsing.

Layout follows Apple's *Mac OS Runtime Architectures* PEF chapter.
"""

from __future__ import annotations

import datetime as dt
import struct
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .errors import HostError

MAC_EPOCH = dt.datetime(1904, 1, 1, tzinfo=dt.UTC)
SECTION_KINDS = {
    0: "code",
    1: "unpacked_data",
    2: "pattern_initialized_data",
    3: "constant",
    4: "loader",
    5: "debug",
    6: "executable_data",
    7: "exception",
    8: "traceback",
}


class PEFError(ValueError):
    """The input is not a valid, sufficiently complete PEF container."""


@dataclass(frozen=True)
class Section:
    """The PEF section metadata needed to materialize a section image."""

    index: int
    kind: str
    default_address: int
    total_size: int
    unpacked_size: int
    packed_size: int
    container_offset: int
    alignment: int


@dataclass(frozen=True)
class Import:
    """An imported symbol and the library that provides it."""

    index: int
    name: str
    library: str | None
    weak: bool


def cstr(buf: bytes, off: int) -> str:
    if off < 0 or off >= len(buf):
        return ""
    end = buf.find(b"\0", off)
    return buf[off : (len(buf) if end < 0 else end)].decode("mac_roman", "replace")


def varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = 0
    while True:
        if pos >= len(buf):
            raise PEFError("truncated variable-length integer")
        b = buf[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos


def mac_date(seconds: int) -> str:
    try:
        return (
            (MAC_EPOCH + dt.timedelta(seconds=seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OverflowError, ValueError):
        return str(seconds)


def u16(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">H", buf, offset)[0]


def u32(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">I", buf, offset)[0]


class PEF:
    """Parse a PEF container and expose its loader metadata and images."""

    def __init__(self, data: bytes):
        self.data = data
        if len(data) < 40 or data[:4] != b"Joy!" or data[4:8] != b"peff":
            raise PEFError("not a PEF container")

        self.header = {
            "tag1": data[:4].decode("ascii", "replace"),
            "tag2": data[4:8].decode("ascii", "replace"),
            "architecture": data[8:12].decode("ascii", "replace"),
            "formatVersion": u32(data, 12),
            "dateTimeStamp": u32(data, 16),
            "dateTime": mac_date(u32(data, 16)),
            "oldDefVersion": u32(data, 20),
            "oldImpVersion": u32(data, 24),
            "currentVersion": u32(data, 28),
            "sectionCount": u16(data, 32),
            "instSectionCount": u16(data, 34),
            "reserved": u32(data, 36),
        }

        self.sections: list[Section] = []
        for index in range(self.header["sectionCount"]):
            offset = 40 + index * 28
            if offset + 28 > len(data):
                raise PEFError("truncated section table")
            values = struct.unpack_from(">iIIIII4B", data, offset)
            self.sections.append(
                Section(
                    index=index,
                    kind=SECTION_KINDS.get(values[6], f"unknown_{values[6]}"),
                    default_address=values[1],
                    total_size=values[2],
                    unpacked_size=values[3],
                    packed_size=values[4],
                    container_offset=values[5],
                    alignment=1 << values[8],
                )
            )

        self.loader_section = next(
            (section for section in self.sections if section.kind == "loader"), None
        )
        self.loader = (
            self._section_bytes(self.loader_section) if self.loader_section else b""
        )
        self.loader_header = self._parse_loader_header() if self.loader else {}
        self.libraries, self.imports = (
            self._parse_imports() if self.loader else ([], [])
        )
        self.reloc_headers = self._parse_reloc_headers() if self.loader else []

    @classmethod
    def from_bytes(cls, data: bytes) -> PEF:
        """Parse *data* as a PEF container."""
        return cls(data)

    def _section_bytes(self, section: Section | None) -> bytes:
        if section is None:
            return b""
        end = section.container_offset + section.packed_size
        if section.container_offset < 0 or end > len(self.data):
            raise PEFError(f"section {section.index} exceeds file")
        return self.data[section.container_offset : end]

    def raw_section(self, index: int) -> bytes:
        """Return the bytes stored for section *index*, before expansion."""
        return self._section_bytes(self.sections[index])

    def _parse_loader_header(self) -> dict[str, int]:
        if len(self.loader) < 56:
            raise PEFError("truncated loader header")
        names = [
            "mainSection",
            "mainOffset",
            "initSection",
            "initOffset",
            "termSection",
            "termOffset",
            "importedLibraryCount",
            "totalImportedSymbolCount",
            "relocSectionCount",
            "relocInstrOffset",
            "loaderStringsOffset",
            "exportHashOffset",
            "exportHashTablePower",
            "exportedSymbolCount",
        ]
        values = struct.unpack_from(">iIiIiIIIIIIIII", self.loader, 0)
        return dict(zip(names, values, strict=True))

    def _parse_imports(self) -> tuple[list[dict[str, Any]], list[Import]]:
        header = self.loader_header
        position = 56
        libraries: list[dict[str, Any]] = []
        for index in range(header["importedLibraryCount"]):
            if position + 24 > len(self.loader):
                raise PEFError("truncated library table")
            (
                name_offset,
                old_version,
                current_version,
                count,
                first,
                options,
                _init_order,
                _reserved,
            ) = struct.unpack_from(">IIIII BBH", self.loader, position)
            position += 24
            libraries.append(
                {
                    "index": index,
                    "offset": position - 24,
                    "nameOffset": name_offset,
                    "oldImpVersion": old_version,
                    "currentVersion": current_version,
                    "importedSymbolCount": count,
                    "firstImportedSymbol": first,
                    "options": options,
                    "initBeforeClient": bool(options & 0x80),
                    "weak": bool(options & 0x40),
                    "name": cstr(
                        self.loader,
                        header["loaderStringsOffset"] + name_offset,
                    ),
                }
            )

        imports: list[Import] = []
        for index in range(header["totalImportedSymbolCount"]):
            if position + 4 > len(self.loader):
                raise PEFError("truncated import table")
            flags = self.loader[position]
            name_offset = int.from_bytes(
                self.loader[position + 1 : position + 4], "big"
            )
            position += 4
            library = next(
                (
                    library
                    for library in libraries
                    if library["firstImportedSymbol"]
                    <= index
                    < library["firstImportedSymbol"] + library["importedSymbolCount"]
                ),
                None,
            )
            imports.append(
                Import(
                    index=index,
                    name=cstr(
                        self.loader,
                        header["loaderStringsOffset"] + name_offset,
                    ),
                    library=library["name"] if library else None,
                    weak=bool(flags & 0x80),
                )
            )

        self._after_imports = position
        return libraries, imports

    def _parse_reloc_headers(self) -> list[dict[str, int]]:
        position = self._after_imports
        headers: list[dict[str, int]] = []
        for _ in range(self.loader_header["relocSectionCount"]):
            if position + 12 > len(self.loader):
                raise PEFError("truncated relocation header")
            section_index, _reserved, count, first = struct.unpack_from(
                ">HHII", self.loader, position
            )
            headers.append(
                {
                    "sectionIndex": section_index,
                    "relocCountBlocks": count,
                    "firstRelocOffset": first,
                }
            )
            position += 12
        return headers

    def pidata(self, expand: bool = True) -> dict[str, Any]:
        section = next(
            (
                candidate
                for candidate in self.sections
                if candidate.kind == "pattern_initialized_data"
            ),
            None,
        )
        if section is None:
            return {
                "instructions": [],
                "opcodeCounts": {},
                "expanded": b"",
                "unpackedSize": 0,
                "totalSize": 0,
            }

        packed = self.raw_section(section.index)
        position = 0
        output = bytearray()
        instructions: list[dict[str, Any]] = []
        counts: Counter[int] = Counter()
        while position < len(packed) and len(output) < section.unpacked_size:
            at = position
            byte = packed[position]
            position += 1
            opcode = byte >> 5
            count = byte & 31
            if count == 0:
                count, position = varint(packed, position)
            counts[opcode] += 1
            instruction: dict[str, Any] = {
                "offset": at,
                "opcode": opcode,
                "count": count,
            }
            if opcode == 0:
                output.extend(b"\0" * count)
            elif opcode == 1:
                instruction["rawOffset"] = position
                instruction["dataLength"] = count
                output.extend(packed[position : position + count])
                position += count
            elif opcode == 2:
                repeats, position = varint(packed, position)
                instruction["repeatCount"] = repeats + 1
                instruction["rawOffset"] = position
                raw = packed[position : position + count]
                position += count
                output.extend(raw * (repeats + 1))
            elif opcode in (3, 4):
                if count:
                    common = count
                else:
                    common, position = varint(packed, position)
                custom, position = varint(packed, position)
                repeats, position = varint(packed, position)
                instruction.update(
                    commonSize=common,
                    customSize=custom,
                    repeatCount=repeats,
                    rawOffset=position,
                )
                if opcode == 3:
                    common_data = packed[position : position + common]
                    position += common
                    for repeat in range(repeats):
                        output.extend(common_data)
                        output.extend(
                            packed[
                                position + repeat * custom : position
                                + (repeat + 1) * custom
                            ]
                        )
                    position += custom * repeats
                    output.extend(common_data)
                else:
                    custom_data = packed[position : position + custom * repeats]
                    position += custom * repeats
                    for repeat in range(repeats):
                        output.extend(b"\0" * common)
                        output.extend(
                            custom_data[repeat * custom : (repeat + 1) * custom]
                        )
                    output.extend(b"\0" * common)
            else:
                instruction["reserved"] = True
            instructions.append(instruction)

        if len(output) != section.unpacked_size:
            raise PEFError(
                f"PIDATA expanded {len(output)} != unpackedSize {section.unpacked_size}"
            )
        expanded = bytes(output) + b"\0" * (section.total_size - len(output))
        return {
            "instructions": instructions,
            "opcodeCounts": {str(key): value for key, value in counts.items()},
            "expanded": expanded if expand else b"",
            "unpackedSize": section.unpacked_size,
            "totalSize": section.total_size,
            "packedSize": section.packed_size,
        }

    def section_image(self, index: int) -> bytes:
        """Return the materialized image of section *index*."""
        section = self.sections[index]
        if section.kind == "pattern_initialized_data":
            image = self.pidata()["expanded"]
        else:
            image = self.raw_section(index)
        return image[: section.total_size].ljust(section.total_size, b"\0")

    def exports(self) -> dict[str, tuple[int, int]]:
        """Return exported names mapped to ``(section index, offset)``."""
        header = self.loader_header
        power = header["exportHashTablePower"]
        key_offset = header["exportHashOffset"] + (1 << power) * 4
        symbols_offset = key_offset + 4 * header["exportedSymbolCount"]
        exports: dict[str, tuple[int, int]] = {}
        for index in range(header["exportedSymbolCount"]):
            length = u16(self.loader, key_offset + index * 4)
            encoded_name_offset = u16(self.loader, key_offset + index * 4 + 2)
            class_and_name_offset = u32(self.loader, symbols_offset + index * 10)
            value = u32(self.loader, symbols_offset + index * 10 + 4)
            section_index = struct.unpack_from(
                ">h", self.loader, symbols_offset + index * 10 + 8
            )[0]
            name_offset = class_and_name_offset & 0xFFFFFF
            name = self.loader[
                header["loaderStringsOffset"] + name_offset : header[
                    "loaderStringsOffset"
                ]
                + name_offset
                + length
            ].decode("mac_roman", "replace")
            # The key table's encoded offset is parsed above exactly as in PEF;
            # the symbol record's name offset is the one used by the format.
            _ = encoded_name_offset
            exports[name] = (section_index, value)
        return exports

    def reloc_stream(self, index: int) -> bytes:
        """Return the raw relocation instruction stream for header *index*."""
        header = self.reloc_headers[index]
        start = self.loader_header["relocInstrOffset"] + header["firstRelocOffset"]
        end = start + header["relocCountBlocks"] * 2
        if start < 0 or end > len(self.loader):
            raise PEFError(f"relocation stream {index} exceeds loader section")
        return self.loader[start:end]


# Relocation opcodes are specified in Mac OS Runtime Architectures, chapter 8.
# This interpreter deliberately keeps the PEF stream separate from section data:
# PEF images contain pre-linked addresses, so callers supply section deltas.
def apply_relocations(
    section: bytearray,
    stream: bytes,
    *,
    section_deltas: list[int],
    imports: list[int],
    initial_c: int,
    initial_d: int,
) -> None:
    """Apply a PEF relocation instruction stream in place."""
    if len(stream) % 2:
        raise HostError("relocation stream has an odd byte length")

    words = [struct.unpack_from(">H", stream, i)[0] for i in range(0, len(stream), 2)]
    pos = 0
    import_index = 0
    current_c = initial_c
    current_d = initial_d
    history: list[tuple[int, int, str]] = []

    def add_word(value: int) -> None:
        if pos < 0 or pos + 4 > len(section):
            raise HostError(f"relocation position out of range: {pos:#x}")
        old = struct.unpack_from(">I", section, pos)[0]
        struct.pack_into(">I", section, pos, (old + value) & 0xFFFF_FFFF)

    def delta(index: int) -> int:
        if index < 0 or index >= len(section_deltas):
            raise HostError(f"relocation references section {index}")
        return section_deltas[index]

    def imp(index: int) -> int:
        if index < 0 or index >= len(imports):
            raise HostError(f"relocation references import {index}")
        return imports[index]

    def execute(token: tuple[int, int, str], repeating: bool = False) -> None:
        """Execute one decoded instruction token; repeats replay tokens."""
        nonlocal pos, import_index, current_c, current_d
        offset, length, kind = token
        word = words[offset]
        low9 = word & 0x1FF
        if kind == "skip":
            skip = (word >> 6) & 0xFF
            count = word & 0x3F
            pos += 4 * skip
            for _ in range(count):
                add_word(current_d)
                pos += 4
        elif kind in ("c", "d"):
            value = current_c if kind == "c" else current_d
            for _ in range(low9 + 1):
                add_word(value)
                pos += 4
        elif kind == "tv12":
            for _ in range(low9 + 1):
                add_word(current_c)
                pos += 4
                add_word(current_d)
                pos += 4
                pos += 4
        elif kind == "tv8":
            for _ in range(low9 + 1):
                add_word(current_c)
                pos += 4
                add_word(current_d)
                pos += 4
        elif kind == "vtable8":
            for _ in range(low9 + 1):
                add_word(current_d)
                pos += 8
        elif kind == "import_run":
            for _ in range(low9 + 1):
                add_word(imp(import_index))
                import_index += 1
                pos += 4
        elif kind == "sm_import":
            index = low9
            add_word(imp(index))
            import_index = index + 1
            pos += 4
        elif kind == "sm_c":
            current_c = delta(low9)
        elif kind == "sm_d":
            current_d = delta(low9)
        elif kind == "sm_section":
            add_word(delta(low9))
            pos += 4
        elif kind == "incr":
            pos += (word & 0xFFF) + 1
        elif kind in ("sm_repeat", "lg_repeat"):
            if repeating:
                raise HostError("nested relocation repeats are illegal")
            if kind == "sm_repeat":
                block_words = ((word >> 8) & 0xF) + 1
                repeat_count = (word & 0xFF) + 1
            else:
                if length != 2:
                    raise HostError("malformed long relocation repeat")
                block_words = ((word >> 6) & 0xF) + 1
                repeat_count = ((word & 0x3F) << 16) | words[offset + 1]
            selected: list[tuple[int, int, str]] = []
            total = 0
            for prior in reversed(history):
                selected.append(prior)
                total += prior[1]
                if total == block_words:
                    break
                if total > block_words:
                    raise HostError("relocation repeat splits an instruction")
            if total != block_words:
                raise HostError("relocation repeat has no complete preceding block")
            selected.reverse()
            for _ in range(repeat_count):
                for prior in selected:
                    execute(prior, repeating=True)
        elif kind == "set_position":
            pos = (word & 0x3FF) << 16 | words[offset + 1]
        elif kind == "lg_import":
            index = (word & 0x3FF) << 16 | words[offset + 1]
            add_word(imp(index))
            import_index = index + 1
            pos += 4
        elif kind == "lg_section":
            subop = (word >> 6) & 0xF
            index = (word & 0x3F) << 16 | words[offset + 1]
            if subop == 0:
                add_word(delta(index))
                pos += 4
            elif subop == 1:
                current_c = delta(index)
            elif subop == 2:
                current_d = delta(index)
            else:
                raise HostError(f"unknown long relocation section sub-op {subop}")
        else:
            raise HostError(f"unknown relocation instruction {kind}")

    stream_offset = 0
    while stream_offset < len(words):
        word = words[stream_offset]
        top2 = word >> 14
        top7 = word >> 9
        top4 = word >> 12
        top6 = word >> 10
        if top2 == 0:
            kind, length = "skip", 1
        elif top7 == 0b0100000:
            kind, length = "c", 1
        elif top7 == 0b0100001:
            kind, length = "d", 1
        elif top7 == 0b0100010:
            kind, length = "tv12", 1
        elif top7 == 0b0100011:
            kind, length = "tv8", 1
        elif top7 == 0b0100100:
            kind, length = "vtable8", 1
        elif top7 == 0b0100101:
            kind, length = "import_run", 1
        elif top7 == 0b0110000:
            kind, length = "sm_import", 1
        elif top7 == 0b0110001:
            kind, length = "sm_c", 1
        elif top7 == 0b0110010:
            kind, length = "sm_d", 1
        elif top7 == 0b0110011:
            kind, length = "sm_section", 1
        elif top4 == 0b1000:
            kind, length = "incr", 1
        elif top4 == 0b1001:
            kind, length = "sm_repeat", 1
        elif top6 == 0b101000:
            kind, length = "set_position", 2
        elif top6 == 0b101001:
            kind, length = "lg_import", 2
        elif top6 == 0b101100:
            kind, length = "lg_repeat", 2
        elif top6 == 0b101101:
            kind, length = "lg_section", 2
        else:
            raise HostError(f"unknown relocation opcode {word:#06x}")
        if stream_offset + length > len(words):
            raise HostError(f"truncated relocation instruction at word {stream_offset}")
        token = (stream_offset, length, kind)
        execute(token)
        history.append(token)
        stream_offset += length
