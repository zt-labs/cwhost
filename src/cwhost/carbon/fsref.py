"""Carbon FSRef catalog/volume APIs backed by :mod:`cwhost.vfs`."""

from __future__ import annotations

from typing import Any

from .. import abi
from ..errors import HostError
from ..vfs import decode_fsref, encode_fsref
from . import shim


def _unicode_name(guest: Any, pointer: int, length: int) -> str:
    if length == 0:
        return ""
    try:
        return guest.read(pointer, length * 2).decode("utf-16-be")
    except UnicodeDecodeError as error:
        raise HostError("invalid UTF-16 FSRef name") from error


def _set_err(guest: Any, value: int) -> int:
    guest.ret(value & 0xFFFF_FFFF)
    return value


@shim("FSMakeFSRefUnicode")
def FSMakeFSRefUnicode(session: Any, guest: Any) -> int:
    parent_ptr, length, name_ptr, _encoding, out_ptr = guest.args(
        ("ptr", "u32", "ptr", "u32", "ptr")
    )
    parent = decode_fsref(guest.read(parent_ptr, 80))
    text = _unicode_name(guest, name_ptr, length)
    for child in parent.children_named(text):
        if child.name == text:
            guest.write(out_ptr, encode_fsref(child))
            return _set_err(guest, 0)
    return _set_err(guest, -43)


@shim("FSGetVolumeInfo")
def FSGetVolumeInfo(session: Any, guest: Any) -> int:
    # FSGetVolumeInfo(volume, index, volumeRefNum, whichInfo, info,
    #                  volumeName, rootDirectory).  The compiler only needs
    # the synthetic Build volume identity and root; zeroed optional metadata
    # is valid for the bitmap fields it does not consume.
    volume, index, volume_out, _which_info, info_ptr, name_ptr, root_ptr = guest.args(
        ("s16", "u32", "ptr", "u32", "ptr", "ptr", "ptr")
    )
    # Files.h FSGetVolumeInfo: volumeIndex 0 means use `volume`; a nonzero
    # index walks the volume list, which this host does not implement.
    if index:
        raise HostError(f"FSGetVolumeInfo volumeIndex {index} is not implemented")
    volume_name = "Build" if volume in (0, -1, -2) else "Host"
    root = session.vfs._root(volume_name)
    if volume_out:
        guest.w16(volume_out, -1 if root.volume == "Build" else -2)
    if info_ptr:
        guest.write(info_ptr, b"\0" * 256)
    if name_ptr:
        raw = volume_name.encode("utf-16-be")
        units = [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]
        abi.HFSUniStr255.pack(
            guest, name_ptr, length=len(units), unicode=units + [0] * (255 - len(units))
        )
    if root_ptr:
        guest.write(root_ptr, encode_fsref(root))
    return _set_err(guest, 0)


@shim("FSGetCatalogInfo")
def FSGetCatalogInfo(session: Any, guest: Any) -> int:
    # FSGetCatalogInfo(ref, whichInfo, catalogInfo, outName, outFSSpec,
    #                   outParentRef).  The compiler uses this to inspect the
    # source and prefix files.  Fill the complete structure so callers may
    # request any bitmap without receiving uninitialised guest memory.
    ref_ptr, _which_info, catalog_ptr, name_ptr, spec_ptr, parent_ptr = guest.args(
        ("ptr", "u32", "ptr", "ptr", "ptr", "ptr")
    )
    node = decode_fsref(guest.read(ref_ptr, 80))
    parent = node.parent or node
    if catalog_ptr:
        vref, parent_id, _name = node.vfs.fsspec(node)
        is_dir = node.is_dir
        data_len = len(node.data_fork()) if not is_dir else 0
        rsrc_len = len(node.resource_fork()) if not is_dir else 0
        abi.FSCatalogInfo.pack(
            guest,
            catalog_ptr,
            nodeFlags=0x8000 if is_dir else 0,
            volume=vref,
            parentDirID=parent_id,
            nodeID=node.id,
            sharingFlags=0,
            userPrivileges=0,
            reserved1=0,
            reserved2=0,
            createDate=b"\0" * 8,
            contentModDate=b"\0" * 8,
            attributeModDate=b"\0" * 8,
            accessDate=b"\0" * 8,
            backupDate=b"\0" * 8,
            permissions=b"\0" * 16,
            finderInfo=node.finder_info()[:16],
            extFinderInfo=b"\0" * 16,
            dataLogicalSize=data_len,
            dataPhysicalSize=data_len,
            rsrcLogicalSize=rsrc_len,
            rsrcPhysicalSize=rsrc_len,
            valence=len(node.children()) if is_dir else 0,
            textEncodingHint=0,
        )
    if name_ptr:
        raw = node.name.encode("utf-16-be")
        units = [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]
        if len(units) > 255:
            raise HostError(f"FSRef name too long: {node.name!r}")
        abi.HFSUniStr255.pack(
            guest, name_ptr, length=len(units), unicode=units + [0] * (255 - len(units))
        )
    if spec_ptr:
        vref, parent_id, name = node.vfs.fsspec(node)
        abi.FSSpec.pack(guest, spec_ptr, vRefNum=vref, parID=parent_id, name=name)
    if parent_ptr:
        guest.write(parent_ptr, encode_fsref(parent))
    return _set_err(guest, 0)
