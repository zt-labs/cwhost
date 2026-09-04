import re
import shutil
import subprocess

import pytest

from cwhost.resources import ResourceFork
from cwhost.vfs import VFS


def test_compiler_strings_match_derez(compiler_83):
    if not shutil.which("DeRez"):
        pytest.skip("DeRez not installed")
    fork = ResourceFork.parse(
        VFS(build_root=compiler_83.parent).node(compiler_83).resource_fork()
    )
    strings = fork.str_list(10100)
    derez = subprocess.run(
        ["DeRez", "-only", "'STR#'(10100)", str(compiler_83)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # Recent DeRez versions render STR# payloads as consecutive $"..."
    # hex runs with comments, not as one contiguous quoted string.  Decode
    # the independent representation before comparing its first Pascal string.
    hex_payload = b"".join(
        bytes.fromhex(run.replace(" ", ""))
        for run in re.findall(r'\$"([0-9a-fA-F ]+)"', derez)
    )
    assert strings and len(hex_payload) >= 3
    assert int.from_bytes(hex_payload[:2], "big") >= 1
    length = hex_payload[2]
    assert strings[0] == hex_payload[3 : 3 + length]
