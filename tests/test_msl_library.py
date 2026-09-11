"""Compile the MSL C sources under the CodeWarrior root and compare each object
with the member the shipped MSL_C_PPC.Lib carries.

This is a Pro 8.0 check: the library on the CD was built by the 8.0 plug-in and
the 8.1-8.3 updaters ship no rebuilt library, so a newer root fails here rather
than skipping.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from cwhost.cli import cwcc

LIBRARY = Path("MSL/MSL_C/MSL_MacOS/Lib/PPC/MSL_C_PPC.Lib")
SOURCE_DIRS = (Path("MSL/MSL_C/MSL_Common/Src"), Path("MSL/MSL_C/MSL_MacOS/Src"))
PREFIX = Path("MSL/MSL_C/MSL_MacOS/Include/ansi_prefix.mac.h")
FLAGS = Path("tests/fixtures/flags/MSL C.PPC.MTrgt-MSL C PPC Release.txt")


def library_members(data: bytes) -> dict[str, bytes]:
    """Member name -> object bytes, from the MWOB library member table."""
    if data[:4] != b"MWOB":
        raise ValueError("not an MWOB library")
    (count,) = struct.unpack_from(">I", data, 24)
    members: dict[str, bytes] = {}
    for row in range(28, 28 + count * 20, 20):
        name_offset, data_offset, size = struct.unpack_from(">I4xII", data, row + 4)
        name = data[name_offset : data.index(b"\0", name_offset)].decode("mac_roman")
        members[name] = data[data_offset : data_offset + size]
    return members


def _member_names() -> list[str]:
    """Parametrize at collection time; the cw_root fixture handles skipping."""
    root = os.environ.get("CWHOST_CW_ROOT")
    if not root:
        return []
    library = Path(root).expanduser() / LIBRARY
    if not library.is_file():
        return []
    return sorted(library_members(library.read_bytes()))


@pytest.mark.parametrize("member", _member_names() or [None])
def test_member_matches_shipped_library(member, cw_root, tmp_path):
    if member is None:
        pytest.skip(f"{LIBRARY} not present under CWHOST_CW_ROOT")
    source = next(
        (cw_root / d / member for d in SOURCE_DIRS if (cw_root / d / member).is_file()),
        None,
    )
    assert source is not None, f"no source for {member} under {cw_root}"
    out = tmp_path / "out.o"
    status = cwcc.main(
        [
            "--cw-root",
            str(cw_root),
            *FLAGS.read_text().split(),
            "-prefix",
            str(cw_root / PREFIX),
            "-c",
            str(source),
            "-o",
            str(out),
        ]
    )
    assert status == 0
    assert out.read_bytes() == library_members((cw_root / LIBRARY).read_bytes())[member]
