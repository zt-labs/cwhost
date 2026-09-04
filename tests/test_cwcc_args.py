"""cwcc argument mapping: flags → records, include order, leftover flags."""

from pathlib import Path

import pytest
from test_panels import CASES

from cwhost import panels
from cwhost.cli import cwcc
from cwhost.cli.flags import from_flags
from cwhost.session import Session, TargetInfo
from cwhost.vfs import VFS


@pytest.mark.parametrize("fixture,records", CASES, ids=[c[0] for c in CASES])
def test_cwcc_flag_line_matches_mcp_bytes(fixture, records):
    line = Path("tests/fixtures/flags", fixture).read_text().split()
    _options, built = cwcc._parse(line)
    for name in panels.PANEL_NAMES:
        assert built[name].to_bytes() == records[name], name


def test_from_flags_leaves_host_args_in_order():
    built, rest = from_flags(["-prefix", '""', "-opt", "all", "-c", "a.c", "-Iinc"])
    assert rest == ["-prefix", '""', "-c", "a.c", "-Iinc"]
    assert built["PPC Global Optimizer"].GlobalOptimizer_PPC_optimizationlevel == 4


@pytest.mark.parametrize("spelling", ["''", '""', ""])
def test_empty_prefix_clears_the_stationery_default(spelling):
    options, built = cwcc._parse(["-prefix", spelling, "-c", "a.c", "-o", "a.o"])
    assert options.prefix == Path("")
    assert built["C/C++ Compiler"].MWFrontEnd_C_prefixname == ""


def test_omitted_prefix_keeps_the_stationery_default():
    options, built = cwcc._parse(["-c", "a.c", "-o", "a.o"])
    assert options.prefix is None
    assert built["C/C++ Compiler"].MWFrontEnd_C_prefixname == "MacHeadersCarbon.h"


def test_panel_flags_sharing_a_host_flag_prefix_are_not_host_flags():
    # -compilerpoolsstrings starts with -c and -opt with -o; neither is a
    # source or output argument.
    options, built = cwcc._parse(
        ["-compilerpoolsstrings", "-opt", "all", "-c", "a.c", "-o", "a.o"]
    )
    assert options.source == Path("a.c") and options.output == Path("a.o")
    assert built["PPC CodeGen"].MWCodeGen_PPC_poolconst == 1
    assert built["PPC Global Optimizer"].GlobalOptimizer_PPC_optimizationlevel == 4


def test_attached_include_forms():
    options, _ = cwcc._parse(
        ["-Iuser", "-irsys", "-ir", "more", "-c", "a.c", "-o", "a.o"]
    )
    assert options.include_user == [Path("user")]
    assert options.include_system == [Path("sys"), Path("more")]


def test_help_prints_usage(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cwcc._parse(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "--cw-root" in out and "-dialect" in out


def test_user_includes_precede_source_directory_then_system(tmp_path):
    source = tmp_path / "src" / "a.c"
    source.parent.mkdir()
    source.write_text("int x;\n")
    user = tmp_path / "user"
    extra = tmp_path / "ir"
    user.mkdir()
    extra.mkdir()
    options, _ = cwcc._parse(
        [
            "-I",
            str(user),
            "-ir",
            str(extra),
            "-c",
            str(source),
            "-o",
            str(tmp_path / "a.o"),
        ]
    )
    assert options.include_user == [user]
    assert options.include_system == [extra]
    user_paths = [*list(options.include_user or []), source.parent]
    assert user_paths == [user, source.parent]


def test_fullsearch_false_is_including_then_user_then_system(tmp_path):
    src_dir = tmp_path / "src"
    user_dir = tmp_path / "user"
    sys_dir = tmp_path / "system"
    for directory in (src_dir, user_dir, sys_dir):
        directory.mkdir()
    (src_dir / "a.c").write_text("int main(){}\n")
    (src_dir / "hdr.h").write_text("src\n")
    (user_dir / "hdr.h").write_text("user\n")
    (sys_dir / "hdr.h").write_text("sys\n")
    (user_dir / "only_user.h").write_text("u\n")
    (sys_dir / "only_sys.h").write_text("s\n")
    (user_dir / "sub").mkdir()
    (sys_dir / "sub").mkdir()
    (user_dir / "sub" / "nested.h").write_text("user-nested\n")
    (sys_dir / "sub" / "nested.h").write_text("sys-nested\n")
    vfs = VFS(build_root=tmp_path)
    source = vfs.node(src_dir / "a.c")
    session = Session(
        tmp_path / "plugin",
        vfs=vfs,
        panels={},
        target=TargetInfo(output_type=1, outfile=vfs.node(tmp_path / "a.o")),
        files=[source],
        include_user=[vfs.node(user_dir)],
        include_system=[vfs.node(sys_dir)],
    )
    assert session._find_file("hdr.h", False, 0).host == src_dir / "hdr.h"
    assert session._find_file("only_user.h", False, 0).host == user_dir / "only_user.h"
    assert session._find_file("only_sys.h", False, 0).host == sys_dir / "only_sys.h"
    assert session._find_file("hdr.h", True, 0).host == user_dir / "hdr.h"
    assert (
        session._find_file("sub/nested.h", False, 0).host
        == user_dir / "sub" / "nested.h"
    )
    assert session._find_file("sub/missing.h", False, 0) is None


def test_unknown_argument_is_rejected():
    with pytest.raises(ValueError, match="unknown argument: --not-a-panel-flag"):
        cwcc._parse(["-dialect", "c", "--not-a-panel-flag", "-c", "a.c", "-o", "a.o"])


def test_missing_cw_root_path_names_the_flag(capsys):
    with pytest.raises(ValueError, match="--cw-root requires a path"):
        cwcc._parse(["--cw-root", "-c", "a.c", "-o", "a.o"])
    assert cwcc.main(["--cw-root", "-c", "/tmp/a.c", "-o", "/tmp/a.o"]) == 2
    assert "--cw-root" in capsys.readouterr().err


def test_empty_trace_equals_form_requires_a_path():
    with pytest.raises(ValueError, match="--trace requires a path"):
        cwcc._parse(["--trace=", "-c", "a.c", "-o", "a.o"])


def test_empty_trace_space_form_requires_a_path():
    with pytest.raises(ValueError, match="--trace requires a path"):
        cwcc._parse(["--trace", "", "-c", "a.c", "-o", "a.o"])


def test_empty_trace_code_equals_form_requires_a_path():
    with pytest.raises(ValueError, match="--trace-code requires a path"):
        cwcc._parse(["--trace-code=", "-c", "a.c", "-o", "a.o"])


def test_empty_trace_code_space_form_requires_a_path():
    with pytest.raises(ValueError, match="--trace-code requires a path"):
        cwcc._parse(["--trace-code", "", "-c", "a.c", "-o", "a.o"])
