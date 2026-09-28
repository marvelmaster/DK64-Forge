"""Offline model of DK64's primary 8-byte adjustment records.

Implements the source-proven direct-path scratch triplet update. This is not a
general animation decoder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


RECORD_SIZE = 8
MASTER_INDEX = 1


@dataclass(frozen=True)
class AdjustmentRecord:
    index: int
    weight: int
    selector: Optional[int]
    targets: Optional[tuple[int, int, int]]
    terminator: bool


@dataclass(frozen=True)
class AdjustmentResult:
    t5: tuple[int, int, int]
    t1: tuple[int, int, int]
    records: int
    master1_records: int


@dataclass(frozen=True)
class MasterAdjustmentResult:
    t5: tuple[int, ...]
    t1: tuple[int, ...]
    records: int
    selector_counts: tuple[int, ...]


def _signed8(value: int) -> int:
    return value - 0x100 if value & 0x80 else value


def _signed16(data: bytes, offset: int) -> int:
    value = int.from_bytes(data[offset : offset + 2], "big", signed=False)
    return value - 0x10000 if value & 0x8000 else value


def _signed32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _signed16_result(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def parse_adjustment_records(raw_records: bytes) -> tuple[AdjustmentRecord, ...]:
    """Parse full 8-byte records through the source-defined zero marker."""
    records = []
    for offset in range(0, len(raw_records) - RECORD_SIZE + 1, RECORD_SIZE):
        record = raw_records[offset : offset + RECORD_SIZE]
        weight = _signed8(record[0])
        if weight == 0:
            records.append(AdjustmentRecord(offset // RECORD_SIZE, 0, None, None, True))
            return tuple(records)
        records.append(AdjustmentRecord(
            offset // RECORD_SIZE,
            weight,
            record[1],
            tuple(_signed16(record, part) for part in (2, 4, 6)),
            False,
        ))
    raise ValueError("adjustment records lack a complete 8-byte zero-marker terminator")


def apply_master1_adjustments(
    raw_records: bytes,
    t5: tuple[int, int, int],
    t1: tuple[int, int, int],
) -> AdjustmentResult:
    """Apply terminated records selecting master index 1 to primary ``t5``.

    Record layout follows ``func_global_asm_8061A1A0``:
    signed weight at +0, unsigned master selector at +1, and three signed
    big-endian target halfwords at +2/+4/+6. A zero byte at +0 terminates the
    list. The routine changes only ``t5``; ``t1`` is passed through unchanged.

    Arithmetic mirrors the MIPS low-word multiply followed by arithmetic
    shift-right 7, then the halfword store. The source uses trapping ``add``
    and ``sub``; the bounded signed-halfword operands here cannot overflow
    those 32-bit intermediates for an 8-bit weight.
    """
    if len(t5) != 3 or len(t1) != 3:
        raise ValueError("t5 and t1 must each contain three scratch halfwords")
    current = [_signed16_result(value) for value in t5]
    passthrough = tuple(t1)
    record_count = 0
    master1_count = 0
    for record in parse_adjustment_records(raw_records):
        if record.terminator:
            break
        weight = record.weight
        selector = record.selector
        targets = record.targets
        record_count += 1
        if selector == MASTER_INDEX:
            master1_count += 1
            if weight > 0:
                for axis, target in enumerate(targets):
                    # MIPS MULT writes a 64-bit product; MFLO keeps its low word.
                    scaled = _signed32(weight * target) >> 7
                    current[axis] = _signed16_result(current[axis] + scaled)
            else:
                magnitude = -weight
                for axis, target in enumerate(targets):
                    difference = _signed32(target - current[axis])
                    scaled = _signed32(difference * magnitude) >> 7
                    current[axis] = _signed16_result(current[axis] + scaled)


    return AdjustmentResult(tuple(current), passthrough, record_count, master1_count)


def apply_master_adjustments(
    raw_records: bytes,
    t5: tuple[int, ...],
    t1: tuple[int, ...],
) -> MasterAdjustmentResult:
    """Apply every terminated adjustment row to its selected 3-halfword group.

    Scratch arrays are supplied as unsigned halfword bit patterns. The source
    blends signed t5 values and signed targets; t1 is returned unchanged.
    Selector values must address a complete group in the supplied arrays.
    """
    if len(t5) != len(t1) or len(t5) == 0 or len(t5) % 3:
        raise ValueError("t5 and t1 must contain the same nonempty whole number of triplets")
    current = [_signed16_result(value) for value in t5]
    passthrough = tuple(value & 0xFFFF for value in t1)
    counts = [0] * (len(current) // 3)
    record_count = 0
    for record in parse_adjustment_records(raw_records):
        if record.terminator:
            break
        record_count += 1
        selector = record.selector
        if selector >= len(counts):
            raise ValueError(f"adjustment selector {selector} exceeds supplied scratch groups")
        counts[selector] += 1
        if record.weight > 0:
            magnitude = record.weight
            for axis, target in enumerate(record.targets):
                at = selector * 3 + axis
                scaled = _signed32(magnitude * target) >> 7
                current[at] = _signed16_result(current[at] + scaled)
        else:
            magnitude = -record.weight
            for axis, target in enumerate(record.targets):
                at = selector * 3 + axis
                difference = _signed32(target - current[at])
                scaled = _signed32(difference * magnitude) >> 7
                current[at] = _signed16_result(current[at] + scaled)
    return MasterAdjustmentResult(tuple(value & 0xFFFF for value in current),
                                  passthrough, record_count, tuple(counts))
