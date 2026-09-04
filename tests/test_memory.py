from cwhost.guest import Guest
from cwhost.memory import Handles, Heap


def test_handle_resize_preserves_contents_and_locked_handles_do_not_move():
    g = Guest()
    hs = Handles(Heap(g))
    h = hs.new(16)
    g.write(g.u32(h), b"x" * 16)
    hs.resize(h, 1 << 20)
    assert g.read(g.u32(h), 16) == b"x" * 16 and hs.size(h) == 1 << 20
    hs.lock(h)
    assert hs.state(h) & 0x80
    p = g.u32(h)
    hs.resize(h, 32)
    assert g.u32(h) == p and g.read(p, 16) == b"x" * 16
