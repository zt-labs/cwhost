"""compile() applies CompileOptions.prefix to a copy of the panel records."""

from cwhost import panels
from cwhost.api import CompileOptions, compile


def test_compile_sets_prefix_name_without_mutating_caller_panels(cw_root, tmp_path):
    source = tmp_path / "t.c"
    source.write_text("int f(void) { return 0; }\n")
    prefix = cw_root / "MSL/MSL_C/MSL_MacOS/Include/ansi_prefix.mac.h"
    assert prefix.is_file()
    owned = panels.defaults()
    before = owned["C/C++ Compiler"].MWFrontEnd_C_prefixname
    assert before != prefix.name
    result = compile(
        cw_root,
        source,
        CompileOptions(include_user=[], include_system=[], prefix=prefix, panels=owned),
    )
    assert result.status == 0
    assert owned["C/C++ Compiler"].MWFrontEnd_C_prefixname == before
