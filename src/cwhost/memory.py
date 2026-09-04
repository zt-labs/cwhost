"""Classic Macintosh Memory Manager primitives in guest memory."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import HostError
from .guest import Guest


@dataclass
class _Allocation:
    size: int


class Heap:
    """A first-fit guest heap with coalescing free ranges."""

    def __init__(
        self, guest: Guest, base: int = Guest.HEAP_BASE, size: int = 256 << 20
    ):
        if size <= 0:
            raise HostError(f"heap size must be positive, got {size}")
        if base < 0 or base + size > 0x1_0000_0000:
            raise HostError(
                f"heap range is outside PPC32 address space: {base:#x}+{size:#x}"
            )
        self.guest = guest
        self.base = base
        self.size = size
        guest.map(base, size, "rw")
        self._free: list[tuple[int, int]] = [(base, size)]
        self._allocations: dict[int, _Allocation] = {}

    @staticmethod
    def _aligned(value: int, alignment: int) -> int:
        return (value + alignment - 1) // alignment * alignment

    @staticmethod
    def _validate_size(size: int) -> None:
        if not isinstance(size, int) or size < 0:
            raise HostError(f"invalid allocation size {size!r}")

    @staticmethod
    def _validate_align(align: int) -> None:
        if not isinstance(align, int) or align <= 0:
            raise HostError(f"invalid allocation alignment {align!r}")

    def alloc(self, size: int, align: int = 16) -> int:
        self._validate_size(size)
        self._validate_align(align)
        amount = max(size, 1)
        for index, (start, available) in enumerate(self._free):
            address = self._aligned(start, align)
            prefix = address - start
            if prefix + amount > available:
                continue
            suffix_start = address + amount
            suffix = available - prefix - amount
            replacement: list[tuple[int, int]] = []
            if prefix:
                replacement.append((start, prefix))
            if suffix:
                replacement.append((suffix_start, suffix))
            self._free[index : index + 1] = replacement
            self._allocations[address] = _Allocation(amount)
            return address
        raise HostError(f"guest heap exhausted allocating {size} bytes")

    def _allocation(self, address: int) -> _Allocation:
        try:
            return self._allocations[address]
        except KeyError as error:
            raise HostError(
                f"invalid or already freed heap address {address:#x}"
            ) from error

    def _insert_free(self, start: int, amount: int) -> None:
        if amount <= 0:
            return
        self._free.append((start, amount))
        self._free.sort()
        merged: list[tuple[int, int]] = []
        for current_start, current_size in self._free:
            if merged and merged[-1][0] + merged[-1][1] >= current_start:
                previous_start, previous_size = merged[-1]
                end = max(previous_start + previous_size, current_start + current_size)
                merged[-1] = (previous_start, end - previous_start)
            else:
                merged.append((current_start, current_size))
        self._free = merged

    def free(self, address: int) -> None:
        allocation = self._allocations.pop(address, None)
        if allocation is None:
            raise HostError(f"invalid or already freed heap address {address:#x}")
        self._insert_free(address, allocation.size)

    def _shrink(self, address: int, allocation: _Allocation, amount: int) -> int:
        if amount < allocation.size:
            self._insert_free(address + amount, allocation.size - amount)
        allocation.size = amount
        return address

    def _grow_in_place(
        self, address: int, allocation: _Allocation, amount: int
    ) -> bool:
        extra = amount - allocation.size
        if extra <= 0:
            return True
        adjacent_index = next(
            (
                i
                for i, (start, _) in enumerate(self._free)
                if start == address + allocation.size
            ),
            None,
        )
        if adjacent_index is None:
            return False
        start, available = self._free[adjacent_index]
        if available < extra:
            return False
        if available == extra:
            del self._free[adjacent_index]
        else:
            self._free[adjacent_index] = (start + extra, available - extra)
        allocation.size = amount
        return True

    def realloc(self, address: int, size: int, allow_move: bool = True) -> int:
        self._validate_size(size)
        allocation = self._allocation(address)
        amount = max(size, 1)
        if amount <= allocation.size:
            return self._shrink(address, allocation, amount)
        if self._grow_in_place(address, allocation, amount):
            return address
        if not allow_move:
            raise HostError(f"cannot grow locked heap block at {address:#x}")
        new_address = self.alloc(size)
        copy_size = min(allocation.size, amount)
        if copy_size:
            self.guest.write(new_address, self.guest.read(address, copy_size))
        self.free(address)
        return new_address

    @property
    def free_bytes(self) -> int:
        """Return the total bytes currently available to the guest heap."""
        return sum(amount for _start, amount in self._free)

    @property
    def largest_free_block(self) -> int:
        """Return the largest contiguous free block."""
        return max((amount for _start, amount in self._free), default=0)

    def allocated_size(self, address: int) -> int:
        return self._allocation(address).size


@dataclass
class _Handle:
    master: int
    block: int
    requested_size: int
    state_bits: int = 0


class Handles:
    """Relocatable Memory Manager Handles and their master pointers."""

    def __init__(self, heap: Heap):
        self.heap = heap
        self.guest = heap.guest
        self._handles: dict[int, _Handle] = {}

    def _get(self, master: int) -> _Handle:
        try:
            return self._handles[master]
        except KeyError as error:
            raise HostError(f"invalid Memory Manager handle {master:#x}") from error

    def new(self, size: int, clear: bool = False) -> int:
        if not isinstance(size, int) or size < 0:
            raise HostError(f"invalid handle size {size!r}")
        block = self.heap.alloc(size)
        master = self.heap.alloc(4, align=4)
        handle = _Handle(master, block, size)
        self._handles[master] = handle
        self.guest.w32(master, block)
        if clear:
            self.guest.write(block, b"\0" * max(size, 1))
        return master

    def dispose(self, master: int) -> None:
        handle = self._handles.pop(master, None)
        if handle is None:
            raise HostError(f"invalid Memory Manager handle {master:#x}")
        self.heap.free(handle.block)
        self.heap.free(handle.master)

    def size(self, master: int) -> int:
        return self._get(master).requested_size

    def resize(self, master: int, size: int) -> int:
        if not isinstance(size, int) or size < 0:
            raise HostError(f"invalid handle size {size!r}")
        handle = self._get(master)
        old_block = handle.block
        new_block = self.heap.realloc(
            old_block,
            size,
            allow_move=not bool(handle.state_bits & 0x80),
        )
        handle.block = new_block
        handle.requested_size = size
        self.guest.w32(master, new_block)
        return new_block

    def lock(self, master: int) -> None:
        self._get(master).state_bits |= 0x80

    def unlock(self, master: int) -> None:
        self._get(master).state_bits &= 0x7F

    def state(self, master: int) -> int:
        return self._get(master).state_bits

    def set_state(self, master: int, state: int) -> None:
        if not isinstance(state, int) or not 0 <= state <= 0xFF:
            raise HostError(f"invalid handle state {state!r}")
        self._get(master).state_bits = state

    def block(self, master: int) -> int:
        """Return the current block pointer after validating the handle."""
        handle = self._get(master)
        pointer = self.guest.u32(master)
        if pointer != handle.block:
            # Guest code is allowed to observe the master pointer but not to
            # retarget a host-owned handle behind the manager's back.
            raise HostError(f"master pointer for handle {master:#x} was modified")
        return pointer

    def block_size(self, pointer: int) -> int:
        """Return the requested size for a block pointer owned by a handle."""
        for handle in self._handles.values():
            if handle.block == pointer:
                return handle.requested_size
        raise HostError(f"invalid Memory Manager block {pointer:#x}")
