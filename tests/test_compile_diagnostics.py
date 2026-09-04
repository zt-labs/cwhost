"""Compile diagnostics and preprocess: API + cwcc, needs CWHOST_CW_ROOT."""

from __future__ import annotations

from pathlib import Path

from cwhost.api import CompileOptions, compile
from cwhost.cli import cwcc
from cwhost.cli.flags import from_flags


def _c_panels(*extra: str):
    built, rest = from_flags(["-dialect", "c", *extra])
    assert rest == []
    built["C/C++ Compiler"].MWFrontEnd_C_prefixname = ""
    return built


def _cpp_panels(*extra: str):
    built, rest = from_flags(["-dialect", "c++", *extra])
    assert rest == []
    built["C/C++ Compiler"].MWFrontEnd_C_prefixname = ""
    return built


def _cwcc(cw_root: Path, tmp_path: Path, source: Path, extra: list[str]) -> int:
    out = tmp_path / "out.o"
    prefix = tmp_path / "empty.h"
    prefix.write_text("")
    return cwcc.main(
        [
            "--cw-root",
            str(cw_root),
            "-dialect",
            "c",
            "-prefix",
            str(prefix),
            *extra,
            "-c",
            str(source),
            "-o",
            str(out),
        ]
    )


def test_compile_error_is_a_result_not_host_failure(cw_root, tmp_path):
    source = tmp_path / "bad.c"
    source.write_text("#error expected-diagnostic\n")
    result = compile(
        cw_root,
        source,
        CompileOptions(include_user=[], include_system=[], panels=_c_panels()),
    )
    assert result.status != 0
    assert any(
        "preprocessor #error directive" in message.text
        and "expected-diagnostic" in message.source_line
        for message in result.messages
    )


def test_cwcc_compile_error_exits_1(cw_root, tmp_path, capsys):
    source = tmp_path / "bad.c"
    source.write_text("#error expected-diagnostic\n")
    code = _cwcc(cw_root, tmp_path, source, [])
    err = capsys.readouterr().err
    rendered = f"{source.resolve()}:1: error: preprocessor #error directive [10318]\n#error expected-diagnostic\n"
    assert code == 1
    assert err == rendered


def test_compile_warning_is_success_with_message(cw_root, tmp_path):
    source = tmp_path / "warn.c"
    source.write_text("int f(void) { int unused; return 0; }\n")
    result = compile(
        cw_root,
        source,
        CompileOptions(
            include_user=[],
            include_system=[],
            panels=_c_panels("-w", "unusedvar", "-opt", "off"),
        ),
    )
    assert result.status == 0
    assert any(message.level == 1 for message in result.messages)


def test_cwcc_warning_exits_0(cw_root, tmp_path, capsys):
    source = tmp_path / "warn.c"
    source.write_text("int f(void) { int unused; return 0; }\n")
    code = _cwcc(cw_root, tmp_path, source, ["-w", "unusedvar", "-opt", "off"])
    err = capsys.readouterr().err
    assert code == 0
    assert "warning:" in err


def test_preprocess_writes_expanded_text(cw_root, tmp_path):
    source = tmp_path / "pre.c"
    source.write_text("#define TOKEN EXPANDED_OK\nint n = TOKEN;\n")
    result = compile(
        cw_root,
        source,
        CompileOptions(
            include_user=[],
            include_system=[],
            preprocess=True,
            panels=_c_panels(),
        ),
    )
    assert result.status == 0
    assert b"EXPANDED_OK" in result.output
    assert b"TOKEN" not in result.output.replace(b"#define TOKEN", b"")


def test_cwcc_preprocess_exits_0(cw_root, tmp_path, capsys):
    source = tmp_path / "pre.c"
    source.write_text("#define TOKEN EXPANDED_OK\nint n = TOKEN;\n")
    code = _cwcc(cw_root, tmp_path, source, ["-E"])
    capsys.readouterr()
    assert code == 0
    assert b"EXPANDED_OK" in (tmp_path / "out.o").read_bytes()


def test_cpp_compile_error_is_a_result(cw_root, tmp_path):
    source = tmp_path / "bad.cpp"
    source.write_text("#error expected-cpp\n")
    result = compile(
        cw_root,
        source,
        CompileOptions(include_user=[], include_system=[], panels=_cpp_panels()),
    )
    assert result.status != 0
    assert any("expected-cpp" in message.source_line for message in result.messages)


def test_cwcc_cpp_compile_error_exits_1(cw_root, tmp_path, capsys):
    source = tmp_path / "bad.cpp"
    source.write_text("#error expected-cpp\n")
    code = _cwcc(cw_root, tmp_path, source, ["-dialect", "c++"])
    err = capsys.readouterr().err
    assert code == 1
    assert "error:" in err
    assert "expected-cpp" in err


def test_cpp_compile_warning_is_success_with_message(cw_root, tmp_path):
    source = tmp_path / "warn.cpp"
    source.write_text("int f() { int unused; return 0; }\n")
    result = compile(
        cw_root,
        source,
        CompileOptions(
            include_user=[],
            include_system=[],
            panels=_cpp_panels("-w", "unusedvar", "-opt", "off"),
        ),
    )
    assert result.status == 0
    assert any(message.level == 1 for message in result.messages)


def test_cwcc_cpp_warning_exits_0(cw_root, tmp_path, capsys):
    source = tmp_path / "warn.cpp"
    source.write_text("int f() { int unused; return 0; }\n")
    code = _cwcc(
        cw_root, tmp_path, source, ["-dialect", "c++", "-w", "unusedvar", "-opt", "off"]
    )
    err = capsys.readouterr().err
    assert code == 0
    assert "warning:" in err
