"""A small PPC32 big-endian guest used by the CodeWarrior host."""

from __future__ import annotations

import json
import struct
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import unicorn.ppc_const as ppc
from unicorn import (
    UC_ARCH_PPC,
    UC_HOOK_BLOCK,
    UC_HOOK_CODE,
    UC_MODE_BIG_ENDIAN,
    UC_MODE_PPC32,
    UC_PROT_EXEC,
    UC_PROT_READ,
    UC_PROT_WRITE,
    Uc,
)

from .errors import HostError


class Guest:
    TRAP_BASE = 0x2800_0000
    TRAP_SIZE = 0x10000
    STACK_TOP = 0x7000_0000
    HEAP_BASE = 0x3000_0000
    SCRATCH_BASE = 0x2A00_0000
    SCRATCH_SIZE = 16 << 20
    STACK_SIZE = 16 << 20
    PAGE_SIZE = 0x1000

    def __init__(self) -> None:
        self.uc = Uc(UC_ARCH_PPC, UC_MODE_PPC32 | UC_MODE_BIG_ENDIAN)
        self.map(self.TRAP_BASE, self.TRAP_SIZE, "rx")
        self.map(self.STACK_TOP - self.STACK_SIZE, self.STACK_SIZE, "rw")
        self.map(self.SCRATCH_BASE, self.SCRATCH_SIZE, "rw")
        # Unicorn raises on floating-point instructions unless MSR[FP] is set.
        self.uc.reg_write(
            ppc.UC_PPC_REG_MSR,
            self.uc.reg_read(ppc.UC_PPC_REG_MSR) | 0x2000,
        )
        self.set_gpr(1, self.STACK_TOP - 16)
        self._scratch = self.SCRATCH_BASE
        self._trap_next = 0
        self._traps: dict[int, str] = {}
        self._trap_addresses: dict[str, int] = {}
        self._handlers: dict[str, Callable[[Guest], Any]] = {}
        self._inline_handlers: set[str] = set()
        self._pending: int | None = None
        self._return_slot: int | None = None
        self._call_tvectors: list[int] = []
        self._setjmps: dict[int, dict[str, Any]] = {}
        self.trace: list[dict[str, Any]] | None = None
        self._trace_file: Any = None
        self._trace_seq = 0
        self._trace_current: dict[str, Any] | None = None
        self._block_trace_file: Any = None
        self.uc.hook_add(
            UC_HOOK_CODE,
            self._trap_hook,
            begin=self.TRAP_BASE,
            end=self.TRAP_BASE + self.TRAP_SIZE - 1,
        )

    @staticmethod
    def _align_up(value: int, alignment: int) -> int:
        return (value + alignment - 1) & -alignment

    @staticmethod
    def _align_down(value: int, alignment: int) -> int:
        return value & -alignment

    def map(self, addr: int, size: int, perms: str) -> None:
        """Map a page-aligned guest range with ``rwx`` permissions."""
        if size <= 0:
            raise HostError("cannot map an empty guest range")
        if not set(perms) <= set("rwx"):
            raise HostError(f"invalid guest memory permissions: {perms!r}")
        start = self._align_down(addr, self.PAGE_SIZE)
        end = self._align_up(addr + size, self.PAGE_SIZE)
        protection = 0
        if "r" in perms:
            protection |= UC_PROT_READ
        if "w" in perms:
            protection |= UC_PROT_WRITE
        if "x" in perms:
            protection |= UC_PROT_EXEC
        self.uc.mem_map(start, end - start, protection)

    def protect(self, addr: int, size: int, perms: str) -> None:
        """Change permissions on a previously mapped, page-aligned range."""
        if size <= 0:
            return
        if not set(perms) <= set("rwx"):
            raise HostError(f"invalid guest memory permissions: {perms!r}")
        start = self._align_down(addr, self.PAGE_SIZE)
        end = self._align_up(addr + size, self.PAGE_SIZE)
        protection = 0
        if "r" in perms:
            protection |= UC_PROT_READ
        if "w" in perms:
            protection |= UC_PROT_WRITE
        if "x" in perms:
            protection |= UC_PROT_EXEC
        self.uc.mem_protect(start, end - start, protection)

    def read(self, addr: int, n: int) -> bytes:
        return bytes(self.uc.mem_read(addr & 0xFFFF_FFFF, n))

    def write(self, addr: int, data: bytes | bytearray) -> None:
        self.uc.mem_write(addr & 0xFFFF_FFFF, bytes(data))

    def u8(self, addr: int) -> int:
        return self.read(addr, 1)[0]

    def u16(self, addr: int) -> int:
        return struct.unpack(">H", self.read(addr, 2))[0]

    def u32(self, addr: int) -> int:
        return struct.unpack(">I", self.read(addr, 4))[0]

    def w8(self, addr: int, value: int) -> None:
        self.write(addr, bytes((value & 0xFF,)))

    def w16(self, addr: int, value: int) -> None:
        self.write(addr, struct.pack(">H", value & 0xFFFF))

    def w32(self, addr: int, value: int) -> None:
        self.write(addr, struct.pack(">I", value & 0xFFFF_FFFF))

    def gpr(self, n: int) -> int:
        if not 0 <= n <= 31:
            raise HostError(f"invalid PPC GPR {n}")
        return int(self.uc.reg_read(getattr(ppc, f"UC_PPC_REG_{n}"))) & 0xFFFF_FFFF

    def set_gpr(self, n: int, value: int) -> None:
        if not 0 <= n <= 31:
            raise HostError(f"invalid PPC GPR {n}")
        self.uc.reg_write(getattr(ppc, f"UC_PPC_REG_{n}"), value & 0xFFFF_FFFF)

    def fpr(self, n: int) -> float:
        if not 0 <= n <= 31:
            raise HostError(f"invalid PPC FPR {n}")
        bits = int(self.uc.reg_read(ppc.UC_PPC_REG_FPR0 + n)) & 0xFFFF_FFFF_FFFF_FFFF
        return struct.unpack(">d", bits.to_bytes(8, "big"))[0]

    def set_fpr(self, n: int, value: float) -> None:
        if not 0 <= n <= 31:
            raise HostError(f"invalid PPC FPR {n}")
        bits = int.from_bytes(struct.pack(">d", float(value)), "big")
        self.uc.reg_write(ppc.UC_PPC_REG_FPR0 + n, bits)

    def pc(self) -> int:
        return int(self.uc.reg_read(ppc.UC_PPC_REG_PC)) & 0xFFFF_FFFF

    def set_pc(self, value: int) -> None:
        self.uc.reg_write(ppc.UC_PPC_REG_PC, value & 0xFFFF_FFFF)

    def lr(self) -> int:
        return int(self.uc.reg_read(ppc.UC_PPC_REG_LR)) & 0xFFFF_FFFF

    def set_lr(self, value: int) -> None:
        self.uc.reg_write(ppc.UC_PPC_REG_LR, value & 0xFFFF_FFFF)

    def alloc(self, size: int, align: int = 16) -> int:
        if size < 0 or align <= 0 or align & (align - 1):
            raise HostError(f"invalid guest allocation size/alignment: {size}/{align}")
        address = self._align_up(self._scratch, align)
        end = address + max(size, 1)
        if end > self.SCRATCH_BASE + self.SCRATCH_SIZE:
            raise HostError("guest scratch allocation exhausted")
        self._scratch = end
        return address

    def alloc_trap_slot(self, name: str) -> int:
        """Allocate a trap-page word containing the PPC ``blr`` instruction."""
        if name in self._trap_addresses:
            return self._trap_addresses[name]
        if self._trap_next >= self.TRAP_SIZE // 4:
            raise HostError("guest trap page exhausted")
        address = self.TRAP_BASE + self._trap_next * 4
        self._trap_next += 1
        self._traps[address] = name
        self._trap_addresses[name] = address
        self.w32(address, 0x4E80_0020)  # blr
        return address

    def on_trap(
        self, name: str, handler: Callable[[Guest], Any], *, inline: bool = False
    ) -> None:
        """Bind a trap; inline leaf handlers run inside Unicorn's code hook."""
        self._handlers[name] = handler
        if inline:
            self._inline_handlers.add(name)
        else:
            self._inline_handlers.discard(name)

    def _trap_hook(self, uc: Uc, address: int, _size: int, _user: Any) -> None:
        name = self._traps.get(address)
        if self.trace is None and name in self._inline_handlers:
            handler = self._handlers.get(name)
            if handler is None:
                raise HostError(f"no handler bound for inline trap {name}")
            if self._call_tvectors:
                # The CFM import glue exposes the caller descriptor in r12.
                self.set_gpr(12, self._call_tvectors[-1])
            handler(self)
            return
        self._pending = address
        uc.emu_stop()

    def _block_hook(self, _uc: Uc, address: int, _size: int, _user: Any) -> None:
        if self._block_trace_file is not None:
            self._block_trace_file.write(json.dumps({"pc": address}) + "\n")
            self._block_trace_file.flush()

    def enable_trace(self, path: str | Path) -> None:
        if self._trace_file is not None:
            self._trace_file.close()
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        self._trace_file = output.open("w", encoding="utf-8")
        self.trace = []

    def enable_block_trace(self, path: str | Path | None = None) -> None:
        if path is None and self._trace_file is not None:
            self._block_trace_file = self._trace_file
        elif path is not None:
            output = Path(path)
            output.parent.mkdir(parents=True, exist_ok=True)
            self._block_trace_file = output.open("a", encoding="utf-8")
        else:
            raise HostError("block trace requires a path or an enabled trace")
        self.uc.hook_add(UC_HOOK_BLOCK, self._block_hook)

    def args(self, spec: tuple[str, ...]) -> list[Any]:
        """Read arguments according to the CW PPC mixed integer/FPR ABI."""
        valid = {"u32", "ptr", "s16", "u16", "u8", "f64", "u64", "s64"}
        if any(item not in valid for item in spec):
            bad = next(item for item in spec if item not in valid)
            raise HostError(f"unsupported guest argument type {bad!r}")
        values: list[Any] = []
        word = 0
        float_index = 0
        for item in spec:
            if item == "f64":
                values.append(self.fpr(1 + float_index))
                float_index += 1
                word += 2
                continue
            if item in ("u64", "s64"):
                high = (
                    self.gpr(3 + word)
                    if word < 8
                    else self.u32(self.gpr(1) + 24 + 4 * word)
                )
                low = (
                    self.gpr(4 + word)
                    if word + 1 < 8
                    else self.u32(self.gpr(1) + 24 + 4 * (word + 1))
                )
                value = (high << 32) | low
                if item == "s64" and value & (1 << 63):
                    value -= 1 << 64
                values.append(value)
                word += 2
                continue
            raw = (
                self.gpr(3 + word)
                if word < 8
                else self.u32(self.gpr(1) + 24 + 4 * word)
            )
            if item in ("u32", "ptr"):
                value: Any = raw
            elif item == "u8":
                value = raw & 0xFF
            elif item == "u16":
                value = raw & 0xFFFF
            else:
                value = raw & 0xFFFF
                if value & 0x8000:
                    value -= 0x1_0000
            values.append(value)
            word += 1
        return values

    def ret(self, value: int = 0) -> None:
        self.set_gpr(3, value)

    def ret_f64(self, value: float) -> None:
        self.set_fpr(1, value)

    def _trace_trap(self, name: str, address: int, args: list[int], lr: int) -> None:
        if self.trace is None:
            return
        entry: dict[str, Any] = {
            "seq": self._trace_seq,
            "name": name,
            "pc": address,
            "lr": lr,
            "args": args,
        }
        self._trace_seq += 1
        self.trace.append(entry)
        self._trace_current = entry

    def _finish_trace(self, result: int | None) -> None:
        if self._trace_current is not None:
            self._trace_current["result"] = result
            if self._trace_file is not None:
                self._trace_file.write(json.dumps(self._trace_current) + "\n")
                self._trace_file.flush()
            self._trace_current = None

    def run(
        self, pc: int, until: int | None = None, timeout_s: float | None = None
    ) -> None:
        """Run until a return address, a trap handler, or guest termination."""
        current = pc
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise HostError(f"guest execution timed out at {self.pc():#x}")
            self._pending = None
            remaining = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            kwargs: dict[str, Any] = {}
            if remaining is not None:
                kwargs["timeout"] = max(1, int(remaining * 1_000_000))
            self.uc.emu_start(current, until or 0, **kwargs)
            if self._pending is None:
                return
            address = self._pending
            if until is not None and address == until:
                return
            name = self._traps.get(address)
            if name is None:
                raise HostError(f"unallocated trap slot {address:#x}")
            handler = self._handlers.get(name)
            if handler is None:
                raise HostError(f"no handler bound for import {name}")
            old_lr = self.lr()
            if self.trace is not None:
                self._trace_trap(
                    name, address, [self.gpr(i) for i in range(3, 11)], old_lr
                )
            if self._call_tvectors:
                # CFM glue keeps the current caller descriptor in r12 while
                # crossing the import boundary.  This is observable to shims
                # and matters for nested calls with distinct TOCs.
                self.set_gpr(12, self._call_tvectors[-1])
            handler(self)
            if self.trace is not None:
                self._finish_trace(self.gpr(3))
            # A bctrl import clobbers LR with its continuation.  The guest
            # glue normally restores the caller's LR after the import returns;
            # preserving the call() return trap here also makes tiny hand-built
            # guest routines obey that CFM invariant without requiring glue.
            current = self.lr()
            if until is not None and current == old_lr:
                self.set_lr(until)
            if until is not None and current == until:
                return

    def save_regs(self) -> dict[str, Any]:
        """Capture all architectural state touched by plug-in calls."""
        return {
            "gpr": [self.gpr(i) for i in range(32)],
            "fpr": [int(self.uc.reg_read(ppc.UC_PPC_REG_FPR0 + i)) for i in range(32)],
            "pc": self.pc(),
            "lr": self.lr(),
            "ctr": int(self.uc.reg_read(ppc.UC_PPC_REG_CTR)),
            "cr": int(self.uc.reg_read(ppc.UC_PPC_REG_CR)),
            "xer": int(self.uc.reg_read(ppc.UC_PPC_REG_XER)),
            "msr": int(self.uc.reg_read(ppc.UC_PPC_REG_MSR)),
            # UC_PPC_REG_FPSCR exists in current Unicorn; retain a fallback for
            # older bindings that omit it, where no FPSCR state can be queried.
            "fpscr": int(self.uc.reg_read(getattr(ppc, "UC_PPC_REG_FPSCR", 78)))
            if hasattr(ppc, "UC_PPC_REG_FPSCR")
            else 0,
        }

    def restore_regs(self, state: dict[str, Any]) -> None:
        for i, value in enumerate(state["gpr"]):
            self.set_gpr(i, value)
        for i, value in enumerate(state["fpr"]):
            self.uc.reg_write(ppc.UC_PPC_REG_FPR0 + i, value)
        for key, reg in (
            ("pc", ppc.UC_PPC_REG_PC),
            ("lr", ppc.UC_PPC_REG_LR),
            ("ctr", ppc.UC_PPC_REG_CTR),
            ("cr", ppc.UC_PPC_REG_CR),
            ("xer", ppc.UC_PPC_REG_XER),
            ("msr", ppc.UC_PPC_REG_MSR),
        ):
            self.uc.reg_write(reg, state[key])
        if hasattr(ppc, "UC_PPC_REG_FPSCR"):
            self.uc.reg_write(ppc.UC_PPC_REG_FPSCR, state["fpscr"])

    def call(self, tvector: int, *args: int, timeout_s: float | None = None) -> int:
        """Call a CFM routine descriptor while preserving the full guest state."""
        if len(args) > 8:
            raise HostError("guest.call accepts at most eight integer arguments")
        saved = self.save_regs()
        old_r1 = self.gpr(1)
        new_r1 = (old_r1 - 64) & ~0xF
        self._call_tvectors.append(tvector)
        try:
            code = self.u32(tvector)
            toc = self.u32(tvector + 4)
            self.set_gpr(2, toc)
            self.set_gpr(12, tvector)
            for index, value in enumerate(args):
                self.set_gpr(3 + index, value)
            self.set_gpr(1, new_r1)
            self.w32(new_r1, old_r1)
            if self._return_slot is None:
                self._return_slot = self.alloc_trap_slot("__return__")
            self.set_lr(self._return_slot)
            self.set_pc(code)
            self.run(code, until=self._return_slot, timeout_s=timeout_s)
            result = self.gpr(3)
        finally:
            self._call_tvectors.pop()
            self.restore_regs(saved)
        return result

    def setjmp_shim(self, buf: int) -> int:
        """Save a host-side register snapshot for the imported ``setjmp``."""
        state = self.save_regs()
        state["resume_pc"] = self.lr()
        self._setjmps[buf] = state
        self.ret(0)
        return 0

    def longjmp_shim(self, buf: int, value: int) -> None:
        """Restore an imported ``setjmp`` snapshot and arrange driver resume."""
        state = self._setjmps.get(buf)
        if state is None:
            raise HostError(f"longjmp to unknown buffer {buf:#x}")
        self.restore_regs(state)
        result = value or 1
        self.ret(result)
        resume = state["resume_pc"]
        self.set_pc(resume)
        self.set_lr(resume)

    def close(self) -> None:
        for stream in (self._trace_file, self._block_trace_file):
            if stream is not None and stream is not self._trace_file:
                stream.close()
        if self._trace_file is not None:
            self._trace_file.close()
            self._trace_file = None
        self._block_trace_file = None
