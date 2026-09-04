"""A missing include is a compiler diagnostic, not a host failure."""

from __future__ import annotations

import os

import pytest

from cwhost.api import CompileOptions, compile
from cwhost.cli import cwcc
from cwhost.cli.flags import from_flags
from cwhost.errors import HostError, NotFound


def _c_panels():
    built, rest = from_flags(["-dialect", "c"])
    assert rest == []
    built["C/C++ Compiler"].MWFrontEnd_C_prefixname = ""
    return built


def test_missing_include_is_compiler_error(cw_root, tmp_path):
    source = tmp_path / "missing.c"
    source.write_text('#include "does-not-exist.h"\nint x;\n')
    result = compile(
        cw_root,
        source,
        CompileOptions(include_user=[], include_system=[], panels=_c_panels()),
    )
    assert result.status != 0
    assert any("does-not-exist.h" in message.text for message in result.messages)


def test_cwcc_missing_include_exits_1(cw_root, tmp_path, capsys):
    source = tmp_path / "missing.c"
    source.write_text('#include "does-not-exist.h"\nint x;\n')
    prefix = tmp_path / "empty.h"
    prefix.write_text("")
    code = cwcc.main(
        [
            "--cw-root",
            str(cw_root),
            "-dialect",
            "c",
            "-prefix",
            str(prefix),
            "-c",
            str(source),
            "-o",
            str(tmp_path / "out.o"),
        ]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "does-not-exist.h" in err
    assert "unimplemented import" not in err


def test_nested_missing_include_is_compiler_error(cw_root, tmp_path):
    source = tmp_path / "missing.c"
    source.write_text('#include "sub/missing.h"\nint x;\n')
    result = compile(
        cw_root,
        source,
        CompileOptions(include_user=[], include_system=[], panels=_c_panels()),
    )
    assert result.status != 0
    assert any("sub/missing.h" in message.text for message in result.messages)


def test_cwcc_nested_missing_include_exits_1(cw_root, tmp_path, capsys):
    source = tmp_path / "missing.c"
    source.write_text('#include "sub/missing.h"\nint x;\n')
    prefix = tmp_path / "empty.h"
    prefix.write_text("")
    code = cwcc.main(
        [
            "--cw-root",
            str(cw_root),
            "-dialect",
            "c",
            "-prefix",
            str(prefix),
            "-c",
            str(source),
            "-o",
            str(tmp_path / "out.o"),
        ]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "sub/missing.h" in err
    assert "relative include did not resolve uniquely" not in err


def test_dotdot_include_resolves_inside_the_search_root(cw_root, tmp_path):
    inc = tmp_path / "inc"
    (inc / "sub").mkdir(parents=True)
    (inc / "x.h").write_text("int from_dotdot;\n")
    source = tmp_path / "a.c"
    source.write_text('#include "sub/../x.h"\nint x;\n')
    result = compile(
        cw_root,
        source,
        CompileOptions(include_user=[inc], include_system=[], panels=_c_panels()),
    )
    assert result.status == 0


def test_cwcc_dotdot_include_exits_0(cw_root, tmp_path):
    inc = tmp_path / "inc"
    (inc / "sub").mkdir(parents=True)
    (inc / "x.h").write_text("")
    source = tmp_path / "a.c"
    source.write_text('#include "sub/../x.h"\nint x;\n')
    prefix = tmp_path / "empty.h"
    prefix.write_text("")
    code = cwcc.main(
        [
            "--cw-root",
            str(cw_root),
            "-dialect",
            "c",
            "-prefix",
            str(prefix),
            "-I",
            str(inc),
            "-c",
            str(source),
            "-o",
            str(tmp_path / "a.o"),
        ]
    )
    assert code == 0


def test_include_that_escapes_the_search_root_is_compiler_error(
    cw_root, tmp_path, capsys
):
    source = tmp_path / "a.c"
    source.write_text('#include "../../../../etc/passwd"\nint x;\n')
    prefix = tmp_path / "empty.h"
    prefix.write_text("")
    result = compile(
        cw_root,
        source,
        CompileOptions(include_user=[], include_system=[], panels=_c_panels()),
    )
    assert result.status != 0
    assert any("cannot be opened" in message.text for message in result.messages)
    code = cwcc.main(
        [
            "--cw-root",
            str(cw_root),
            "-dialect",
            "c",
            "-prefix",
            str(prefix),
            "-c",
            str(source),
            "-o",
            str(tmp_path / "a.o"),
        ]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "cannot be opened" in err
    assert "unsupported relative include shape" not in err


def test_nested_include_prefers_user_root_over_system(cw_root, tmp_path):
    source = tmp_path / "t.c"
    source.write_text(
        '#include "sub/pick.h"\n#ifndef FROM_USER\n#error not-user\n#endif\nint x;\n'
    )
    user = tmp_path / "user"
    system = tmp_path / "system"
    (user / "sub").mkdir(parents=True)
    (system / "sub").mkdir(parents=True)
    (user / "sub" / "pick.h").write_text("#define FROM_USER 1\n")
    (system / "sub" / "pick.h").write_text("#define FROM_SYSTEM 1\n")
    result = compile(
        cw_root,
        source,
        CompileOptions(
            include_user=[user],
            include_system=[system],
            panels=_c_panels(),
        ),
    )
    assert result.status == 0


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode 000"
)
def test_unreadable_hfs_include_is_host_error(cw_root, tmp_path, capsys):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "x.h").write_text("")
    source = tmp_path / "a.c"
    source.write_text('#include ":locked:x.h"\nint x;\n')
    prefix = tmp_path / "empty.h"
    prefix.write_text("")
    os.chmod(locked, 0)
    try:
        try:
            os.listdir(locked)
        except PermissionError:
            pass
        else:
            pytest.skip("this process can list a mode-000 directory")
        with pytest.raises(HostError, match="locked"):
            compile(
                cw_root,
                source,
                CompileOptions(include_user=[], include_system=[], panels=_c_panels()),
            )
        code = cwcc.main(
            [
                "--cw-root",
                str(cw_root),
                "-dialect",
                "c",
                "-prefix",
                str(prefix),
                "-c",
                str(source),
                "-o",
                str(tmp_path / "a.o"),
            ]
        )
        err = capsys.readouterr().err
        assert code == 2
        assert "locked" in err
        assert "cwcc:" in err
    finally:
        os.chmod(locked, 0o700)


def test_missing_hfs_include_is_not_found_not_io_error(tmp_path):
    from cwhost.vfs import VFS

    vfs = VFS(build_root=tmp_path)
    with pytest.raises(NotFound, match="HFS component not found"):
        vfs.resolve_hfs("Build:absent.h", None)
