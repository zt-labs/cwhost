import struct

from cwhost.pef import apply_relocations


def words(*values):
    return b"".join(struct.pack(">H", value) for value in values)


def u32s(data):
    return struct.unpack(f">{len(data) // 4}I", data)


C, D = 0x1000, 0x2000


def test_by_sect_d_with_skip():
    section = bytearray(16)
    apply_relocations(
        section,
        words((1 << 6) | 2),
        section_deltas=[C, D],
        imports=[],
        initial_c=C,
        initial_d=D,
    )
    assert u32s(section) == (0, D, D, 0)


def test_import_run_adds_to_existing_word_then_tvector12():
    section = bytearray(20)
    struct.pack_into(">I", section, 0, 0x10)
    stream = words((0b0100101 << 9) | 1, (0b0100010 << 9) | 0)
    apply_relocations(
        section,
        stream,
        section_deltas=[C, D],
        imports=[0xAAAA0000, 0xBBBB0000],
        initial_c=C,
        initial_d=D,
    )
    assert u32s(section) == (0xAAAA0010, 0xBBBB0000, C, D, 0)


def test_small_repeat():
    section = bytearray(12)
    stream = words((0b0100000 << 9) | 0, (0b1001 << 12) | (0 << 8) | 1)
    apply_relocations(
        section,
        stream,
        section_deltas=[C, D],
        imports=[],
        initial_c=C,
        initial_d=D,
    )
    assert u32s(section) == (C, C, C)
