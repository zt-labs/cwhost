import struct

from cwhost.guest import Guest


def asm(*w):
    return b"".join(struct.pack(">I", x) for x in w)


def test_trap_receives_register_and_stack_args_and_mixed_double():
    g = Guest()
    seen = {}
    g.alloc_trap_slot("probe")

    def probe(guest):
        seen["v"] = guest.args(("u32", "f64", "ptr", "u32"))
        guest.ret(0x1234)

    g.on_trap("probe", probe, inline=True)
    code = 0x1000_0000
    g.map(code, 0x1000, "rwx")
    # li r3,1 ; li r6,6 ; li r7,7 ; lis r12,0x2800 ; mtctr r12 ; bctrl ; b .
    g.write(
        code,
        asm(
            0x38600001,
            0x38C00006,
            0x38E00007,
            0x3D802800,
            0x7D8903A6,
            0x4E800421,
            0x48000000,
        ),
    )
    g.set_fpr(1, 2.5)
    g.set_gpr(1, Guest.STACK_TOP - 0x100)
    g.run(code, until=code + 6 * 4)
    assert seen["v"] == [1, 2.5, 6, 7] and g.gpr(3) == 0x1234


def test_stack_only_arg_is_at_r1_plus_56():
    g = Guest()
    seen = {}
    g.alloc_trap_slot("probe")
    g.on_trap(
        "probe",
        lambda guest: (seen.update(x=guest.args(("u32",) * 9)[8]), guest.ret(0)),
    )
    code = 0x1000_0000
    g.map(code, 0x1000, "rwx")
    # li r0,9 ; stw r0,56(r1) ; lis r12,0x2800 ; mtctr r12 ; bctrl ; b .
    g.write(
        code,
        asm(0x38000009, 0x90010038, 0x3D802800, 0x7D8903A6, 0x4E800421, 0x48000000),
    )
    g.set_gpr(1, Guest.STACK_TOP - 0x100)
    g.run(code, until=code + 5 * 4)
    assert seen["x"] == 9


def test_fpr_bit_roundtrip():
    g = Guest()
    for value in (0.0, -0.0, 1.5, float("inf")):
        g.set_fpr(3, value)
        assert struct.pack(">d", g.fpr(3)) == struct.pack(">d", value)
