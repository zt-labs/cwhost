import json

from cwhost.session import REQ_INITIALIZE, REQ_TERMINATE, Session, TargetInfo
from cwhost.vfs import VFS


def test_m0(compiler_plugin, tmp_path):
    v = VFS(build_root=tmp_path)
    out = v.node(tmp_path / "x.o")
    s = Session(
        compiler_plugin,
        vfs=v,
        panels={},
        target=TargetInfo(output_type=1, cpu=b"ppc ", os=b"mac ", outfile=out),
        files=[],
        include_user=[],
        include_system=[],
        trace_path=tmp_path / "trace.jsonl",
    )
    s.load()
    f = s.dropin_flags
    assert (
        f["earliestCompatibleAPIVersion"],
        f["newestAPIVersion"],
        f["dropinflags"],
        f["dropintype"],
    ) == (8, 13, 0xB0C00000, b"Comp")
    assert s.request(REQ_INITIALIZE) == 0 and s.request(REQ_TERMINATE) == 0
    s.close()
    names = {
        json.loads(line)["name"]
        for line in (tmp_path / "trace.jsonl").read_text().splitlines()
    }
    assert {"CWGetPluginRequest", "CWDonePluginRequest"} <= names
    assert s.resources.get_ind_string(10100, 1)
