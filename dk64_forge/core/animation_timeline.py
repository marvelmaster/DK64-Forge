"""Narrow candidate cursor conversion for table-11 entry 4.

This does not advance a clock or model 0x80614644's state-flag/range branches.
It retains the pre-override candidate function and adds a state-gated entry
point for the one active entry-4 configuration observed at runtime. It is not
a full reader-state sequence. Endpoint and loop policy are intentionally
left unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct


ENTRY4_LAST_SAMPLE_INDEX = 98


def _f32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", value))[0]


@dataclass(frozen=True)
class Entry4CursorCandidate:
    adjusted_time: float
    cursor0: int
    cursor1: int
    fraction: float
    fraction_bits: int


def entry4_cursor_candidate(sample_time: float) -> Entry4CursorCandidate:
    """Compute the entry-4 adjacent-cursor candidate for adjusted sample time.

    The input is rounded to the binary32 value passed by the DK64 ABI. The
    supported domain is [0, 98): all interior intervals of the 99-record
    asset. The exact endpoint 98 and out-of-range times require the updater's
    state-dependent end/wrap policy and are rejected rather than guessed.
    The returned cursor1 may be overwritten by 0x80614644 depending on its
    selected state pointer and per-state flags/range fields.
    """
    value = _f32(float(sample_time))
    if not math.isfinite(value):
        raise ValueError("sample time must be finite")
    if not 0.0 <= value < ENTRY4_LAST_SAMPLE_INDEX:
        raise ValueError("time is outside entry-4's proven interior interval")
    cursor0 = math.trunc(value)
    cursor1 = cursor0 + 1
    fraction = _f32(value - _f32(float(cursor0)))
    bits = struct.unpack(">I", struct.pack(">f", fraction))[0]
    return Entry4CursorCandidate(value, cursor0, cursor1, fraction, bits)


def entry4_cursor_for_observed_state(
    sample_time: float,
    *,
    range_start: float,
    endpoint_like: float,
    flags: int,
    selected_state: bool,
) -> Entry4CursorCandidate:
    """Return the runtime-observed interior mapping for state (0, 98, 0x3).

    A bounded DK64 runtime capture observed entry 4 selected as AAS+0 with
    range +0x14=0, +0x18=98 and flags +0x1C=0x0003. Interior observations
    followed trunc(time), next cursor, and binary32 fractional remainder.
    The capture did not reach or resolve endpoint behavior. All state inputs
    are explicit; unobserved configurations are rejected.
    """
    if not selected_state:
        raise ValueError("entry-4 state was not the updater-selected state")
    if flags != 0x0003:
        raise ValueError("state flags are outside the runtime-observed configuration")
    if _f32(float(range_start)) != 0.0 or _f32(float(endpoint_like)) != 98.0:
        raise ValueError("range is outside the runtime-observed configuration")
    return entry4_cursor_candidate(sample_time)
