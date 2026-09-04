from cwhost.carbon import TABLE
from cwhost.errors import UnimplementedImport


def test_hfs_parameter_block_imports_are_stubs():
    try:
        TABLE["PBHOpenDFSync"](None, None)
    except UnimplementedImport as error:
        assert error.name == "PBHOpenDFSync"
    else:
        raise AssertionError("expected UnimplementedImport")
