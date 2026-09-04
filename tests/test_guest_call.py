import struct

from cwhost.guest import Guest


def asm(*words):
    return b"".join(struct.pack(">I", word) for word in words)


def test_tvector_call_sets_r2_r12_and_nested_call_restores_outer_state():
    guest = Guest()
    code = 0x1000_0000
    guest.map(code, 0x1000, "rwx")
    guest.write(code, asm(0x7C431378, 0x4E800020))
    guest.write(
        code + 0x100,
        asm(0x3D802800, 0x7D8903A6, 0x4E800421, 0x7C431378, 0x4E800020),
    )
    data = 0x1100_0000
    guest.map(data, 0x1000, "rw")
    guest.write(data, struct.pack(">II", code, 0xAAAA0000))
    guest.write(data + 8, struct.pack(">II", code + 0x100, 0xBBBB0000))
    guest.alloc_trap_slot("nest")
    seen = {}

    def nest(g):
        seen["r12_in_B"] = g.gpr(12)
        seen["inner"] = g.call(data)
        g.ret(0)

    guest.on_trap("nest", nest)
    guest.set_gpr(1, Guest.STACK_TOP - 0x100)
    stack = guest.gpr(1)
    outer = guest.call(data + 8)
    assert seen["inner"] == 0xAAAA0000 and outer == 0xBBBB0000
    assert seen["r12_in_B"] == data + 8 and guest.gpr(1) == stack


def test_setjmp_longjmp():
    guest = Guest()
    buf = 0x1200_0000
    guest.map(buf, 0x1000, "rw")
    guest.set_gpr(1, Guest.STACK_TOP - 0x100)
    guest.set_gpr(5, 0x11)
    guest.set_lr(0xDEAD0000)
    assert guest.setjmp_shim(buf) == 0
    guest.set_gpr(5, 0x55)
    guest.longjmp_shim(buf, 7)
    assert guest.gpr(3) == 7 and guest.gpr(5) == 0x11 and guest.pc() == 0xDEAD0000
