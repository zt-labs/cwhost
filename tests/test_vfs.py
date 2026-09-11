import pytest

from cwhost.appledouble import AppleDouble
from cwhost.errors import HostError
from cwhost.resources import ResourceFork
from cwhost.vfs import VFS, _native_xattr


def test_plugin_resource_fork(compiler_plugin):
    # Not type/creator: the 8.0 plug-in ships with an all-zero Finder info.
    n = VFS(build_root=compiler_plugin.parent).node(compiler_plugin)
    fork = ResourceFork.parse(n.resource_fork())
    assert 10100 in fork.ids(b"STR#")


def test_hfs_paths(cw_root):
    v = VFS(build_root=cw_root)
    src = v.resolve_hfs("Build:MSL:msl_c:MSL_COMMON:src:MEM.C", None)
    assert src.host == (cw_root / "MSL/MSL_C/MSL_Common/Src/mem.c").resolve()
    pre = v.resolve_hfs(":::MSL_MacOS:Include:ansi_prefix.mac.h", src.parent)
    assert (
        pre.host
        == (cw_root / "MSL/MSL_C/MSL_MacOS/Include/ansi_prefix.mac.h").resolve()
    )
    with pytest.raises(HostError):
        v.resolve_hfs("::::::::x", src)


def test_appledouble_preserves_unknown_entries(tmp_path):
    side = tmp_path / "._x"
    ad = AppleDouble.new()
    ad.entries[2] = b"RSRC"
    ad.entries[9] = b"TEXTCWIE" + b"\0" * 24
    ad.entries[7] = b"\x00\x01"
    ad.write(side)
    ad2 = AppleDouble.read(side)
    ad2.entries[2] = b"NEW!"
    ad2.write(side)
    ad3 = AppleDouble.read(side)
    assert ad3.entries == {2: b"NEW!", 9: b"TEXTCWIE" + b"\0" * 24, 7: b"\x00\x01"}


def test_xattrs_round_trip_and_missing_attribute(tmp_path):
    path = tmp_path / "compiler"
    path.write_bytes(b"compiler")
    node = VFS(build_root=tmp_path).node(path)

    assert _native_xattr(path, "com.apple.AttributeThatDoesNotExist") is None
    resource = bytes(range(256))
    node.write_resource_fork(resource)
    assert node.resource_fork() == resource

    finder = b"TEXTCWIE" + b"\0" * 24
    node.set_finder_info(finder)
    assert node.finder_info() == finder


def test_directory_listing_cache_is_invalidated_by_mutations(tmp_path):
    vfs = VFS(build_root=tmp_path)
    directory = vfs.node(tmp_path / "include")
    directory.host.mkdir()
    assert directory.children() == []

    first = vfs.node(directory.host / "first.h")
    first.write_data_fork(b"first")
    assert [child.name for child in directory.children()] == ["first.h"]

    second = vfs.node(directory.host / "second.h")
    second.write_data_fork(b"second")
    assert [child.name for child in directory.children()] == ["first.h", "second.h"]

    renamed = second.rename(directory.host / "renamed.h")
    assert [child.name for child in directory.children()] == ["first.h", "renamed.h"]

    renamed.delete()
    assert [child.name for child in directory.children()] == ["first.h"]


def test_casefolded_child_lookup_is_invalidated_by_mutations(tmp_path):
    vfs = VFS(build_root=tmp_path)
    directory = vfs.node(tmp_path / "include")
    directory.host.mkdir()
    child = vfs.node(directory.host / "Widget.h")
    child.write_data_fork(b"widget")

    assert [match.name for match in directory.children_named("WIDGET.H")] == [
        "Widget.h"
    ]

    child.rename(directory.host / "other.h")
    assert directory.children_named("widget.h") == []
    assert [match.name for match in directory.children_named("OTHER.H")] == ["other.h"]


def test_node_parent_is_stable_within_a_vfs_session(tmp_path):
    vfs = VFS(build_root=tmp_path)
    child = vfs.node(tmp_path / "include" / "widget.h")

    assert child.parent is child.parent
    assert child.parent.host == tmp_path / "include"


def test_text_fork_maps_lf_only_source_without_touching_data(tmp_path):
    vfs = VFS(build_root=tmp_path)
    source = vfs.node(tmp_path / "source.c")
    lf = b"first line\nsecond line\n"
    source.write_data_fork(lf)
    assert source.data_fork() == lf
    assert source.text_fork() == b"first line\rsecond line\r"

    mixed = vfs.node(tmp_path / "mixed.h")
    mixed.write_data_fork(b"first\r\nsecond\n")
    assert mixed.text_fork() == mixed.data_fork()

    binary = vfs.node(tmp_path / "payload.bin")
    binary.write_data_fork(lf)
    assert binary.text_fork() == lf

    binary_pch = vfs.node(tmp_path / "payload.pch")
    binary_pch.write_data_fork(b"header\0bytes\n")
    assert binary_pch.text_fork() == binary_pch.data_fork()
