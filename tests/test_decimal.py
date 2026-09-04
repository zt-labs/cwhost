import struct

from cwhost.carbon import TABLE
from cwhost.errors import UnimplementedImport
from cwhost.session import Harness
from cwhost.vfs import VFS


def test_dec2num_reads_a_decimal_record():
    h = Harness(VFS(build_root="/tmp"))
    g = h.guest
    dec = g.alloc(42)
    coeff = b"1"
    g.write(dec, bytes([0, 0]) + struct.pack(">h", 0) + bytes([len(coeff)]) + coeff)
    g.set_gpr(3, dec)
    TABLE["dec2num"](h, g)
    assert g.fpr(1) == 1.0


def test_num2dec_is_a_stub():
    try:
        TABLE["num2dec"](None, None)
    except UnimplementedImport as error:
        assert error.name == "num2dec"
    else:
        raise AssertionError("expected UnimplementedImport")
