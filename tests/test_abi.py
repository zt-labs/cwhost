from cwhost import abi


def test_pinned_layouts():
    assert abi.HFSUniStr255.size == 512 and abi.CWFileSpec.size == 592
    assert abi.DropInFlags.size == 18 and abi.DropInFlags.offset("dropinflags") == 8
    assert abi.CWFileInfo.offset("filedata") == 8
    assert abi.CWFileInfo.offset("filespec") == 20 and abi.CWFileInfo.size == 614
    t = abi.Struct("T", "mac68k", [("a", "u8"), ("b", "u8[3]"), ("c", "u16")])
    assert t.offset("b") == 1 and t.offset("c") == 4 and t.size == 6
