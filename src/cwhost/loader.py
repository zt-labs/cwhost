"""Materialize relocated PEF fragments in a :mod:`cwhost.guest` instance."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from . import pef
from .errors import HostError, UnimplementedImport
from .guest import Guest


@dataclass
class Fragment:
    pef: pef.PEF
    section_base: list[int]
    image_base: int
    _exports: dict[str, tuple[int, int]]
    _imports: dict[str, int]
    _descriptors: dict[str, int]

    def tvector(self, name: str) -> int:
        try:
            section, offset = self._exports[name]
        except KeyError as error:
            raise HostError(f"unknown fragment export {name}") from error
        if section < 0 or section >= len(self.section_base):
            raise HostError(f"export {name} references section {section}")
        return self.section_base[section] + offset

    @property
    def main_tvector(self) -> int:
        return self._entry_tvector("main")

    @property
    def init_tvector(self) -> int | None:
        return self._optional_entry_tvector("init")

    @property
    def term_tvector(self) -> int | None:
        return self._optional_entry_tvector("term")

    def _entry_tvector(self, name: str) -> int:
        result = self._optional_entry_tvector(name)
        if result is None:
            raise HostError(f"fragment has no {name} entry point")
        return result

    def _optional_entry_tvector(self, name: str) -> int | None:
        header = self.pef.loader_header
        section = header[f"{name}Section"]
        if section < 0:
            return None
        if section >= len(self.section_base):
            raise HostError(f"fragment {name} entry references section {section}")
        return self.section_base[section] + header[f"{name}Offset"]

    def import_descriptor(self, name: str) -> int:
        try:
            return self._descriptors[name]
        except KeyError as error:
            raise HostError(f"unknown fragment import {name}") from error


SECTION_BASE = 0x1000_0000
SECTION_STRIDE = 0x0100_0000
IMAGE_BASE = 0x0F00_0000
DESCRIPTOR_BASE = 0x2900_0000


def _map_image(guest: Guest, address: int, size: int, executable: bool) -> None:
    if size <= 0:
        return
    # Relocations must be written before code becomes RX.  Unicorn's memory
    # protection is applied after the image and all relocation words are in.
    guest.map(address, size, "rwx" if executable else "rw")


def load(guest: Guest, path: Path, imports: dict[str, int]) -> Fragment:
    """Load and relocate a PEF fragment, binding imports to trap slots."""
    path = Path(path)
    container = path.read_bytes()
    parsed = pef.PEF.from_bytes(container)
    missing = sorted(
        {
            item.name
            for item in parsed.imports
            if item.name not in imports and not item.weak
        }
    )
    if missing:
        raise UnimplementedImport(missing[0])

    section_bases = [
        SECTION_BASE + SECTION_STRIDE * section.index for section in parsed.sections
    ]
    for section, base in zip(parsed.sections, section_bases, strict=True):
        if section.total_size:
            _map_image(
                guest,
                base,
                section.total_size,
                section.kind in ("code", "executable_data"),
            )
            guest.write(base, parsed.section_image(section.index))

    image_size = max(len(container), 1)
    guest.map(IMAGE_BASE, image_size, "r")
    guest.write(IMAGE_BASE, container)

    descriptors: dict[str, int] = {}
    descriptor_count = len(parsed.imports)
    if descriptor_count:
        guest.map(DESCRIPTOR_BASE, descriptor_count * 8, "rw")
    import_values: list[int] = []
    for index, item in enumerate(parsed.imports):
        address = DESCRIPTOR_BASE + index * 8
        slot = imports.get(item.name, 0)
        guest.write(address, struct.pack(">II", slot, 0))
        import_values.append(address)
        descriptors.setdefault(item.name, address)

    section_deltas = [
        base - section.default_address
        for base, section in zip(section_bases, parsed.sections, strict=True)
    ]
    for header_index, header in enumerate(parsed.reloc_headers):
        section_index = header["sectionIndex"]
        if section_index < 0 or section_index >= len(parsed.sections):
            raise HostError(f"relocations reference section {section_index}")
        target = bytearray(parsed.section_image(section_index))
        pef.apply_relocations(
            target,
            parsed.reloc_stream(header_index),
            section_deltas=section_deltas,
            imports=import_values,
            initial_c=section_deltas[0] if section_deltas else 0,
            initial_d=section_deltas[1] if len(section_deltas) > 1 else 0,
        )
        guest.write(section_bases[section_index], target)

    # Tighten executable mappings after relocation.  Keep the same page range
    # that Guest.map created, including any tail padding.
    for section, base in zip(parsed.sections, section_bases, strict=True):
        if section.total_size and section.kind in ("code", "executable_data"):
            guest.protect(base, section.total_size, "rx")

    return Fragment(
        pef=parsed,
        section_base=section_bases,
        image_base=IMAGE_BASE,
        _exports=parsed.exports(),
        _imports={item.name: imports.get(item.name, 0) for item in parsed.imports},
        _descriptors=descriptors,
    )
