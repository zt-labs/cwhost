"""Carbon Resource Manager read-side imports."""

from __future__ import annotations

from typing import Any

from ..errors import HostError
from . import shim


def _chain(session: Any):
    for name in ("resources", "resource_chain"):
        chain = (
            session.get(name)
            if isinstance(session, dict)
            else getattr(session, name, None)
        )
        if chain is not None:
            return chain
    raise HostError("Resource Manager has no resource chain")


@shim("GetIndString")
def GetIndString(session: Any, guest: Any) -> None:
    output, list_id, index = guest.args(("ptr", "s16", "s16"))
    value = _chain(session).get_ind_string(list_id, index)
    if len(value) > 255:
        raise HostError(f"STR# string {list_id}/{index} exceeds Str255")
    guest.write(output, bytes([len(value)]) + value + b"\0" * (255 - len(value)))
    guest.ret(0)
