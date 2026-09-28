"""Offline reproduction of the observed 0x80619C2C primary-reader forms."""

from dataclasses import dataclass
import math
import struct


@dataclass(frozen=True)
class DescriptorStep:
    index: int
    descriptor_offset: int
    descriptor: int
    bit_offset: int
    width: int
    first: int
    second: int
    signed_delta: int
    output: int
    secondary_descriptor: int | None = None
    secondary_width: int = 0
    secondary_first: int = 0
    secondary_second: int = 0
    secondary_output: int = 0


@dataclass(frozen=True)
class ReaderResult:
    fraction: float
    scaled_fraction_bits: int
    weight: int
    fpr_bits: tuple[int, int, int]
    t5: bytes
    t1: bytes
    trace: tuple[DescriptorStep, ...]
    descriptor_bytes_consumed: int
    sample_bits_consumed: int


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise ValueError("short descriptor/header")
    return int.from_bytes(data[offset:offset + 2], "big")


def _s16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "big", signed=True)


def _packed(data: bytes, bit_offset: int, width: int) -> int:
    if width == 0:
        return 0
    if bit_offset + width > len(data) * 8:
        raise ValueError("short packed sample")
    return (int.from_bytes(data, "big") >> (len(data) * 8 - bit_offset - width)) & ((1 << width) - 1)


def _s32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _cvt_w_s(value: float, rounding_mode: str) -> int:
    """MIPS CVT.W.S conversion under an explicitly selected FCSR mode."""
    if rounding_mode == "nearest_even":
        result = round(value)
    elif rounding_mode == "toward_zero":
        result = math.trunc(value)
    elif rounding_mode == "toward_positive":
        result = math.ceil(value)
    elif rounding_mode == "toward_negative":
        result = math.floor(value)
    else:
        raise ValueError("unsupported MIPS FCSR rounding mode")
    if not -0x80000000 <= result <= 0x7FFFFFFF:
        raise ValueError("CVT.W.S result is outside signed 32-bit range")
    return result


def _descriptor_plan(descriptors: bytes, output_count: int):
    """Parse per-output descriptors and report exact byte/bit consumption."""
    plan = []
    pos = 0
    bits = 0
    for index in range(output_count):
        descriptor_offset = pos
        desc = _u16(descriptors, pos)
        pos += 2
        width = desc & 0xF
        bits += width
        secondary = None
        secondary_width = 0
        if desc & 0x10:
            secondary = _u16(descriptors, pos)
            pos += 2
            # A zero second descriptor is the width-16 sentinel.
            secondary_width = 16 if secondary == 0 else secondary & 0xF
            bits += secondary_width
        plan.append((index, descriptor_offset, desc, width, secondary,
                     secondary_width))
    return plan, pos, bits


def reproduce(header: bytes, descriptors: bytes, first: bytes, second: bytes,
              fraction_bits: int, *, output_count: int | None = None,
              rounding_mode: str = "nearest_even") -> ReaderResult:
    """Return written scratch outputs for the observed reader form.

    `header` is asset +0x04..+0x13, and descriptors start at asset +0x14.
    Cursor selection and record stride have already selected the two samples.
    The reader's loop budget is three times (count - 1), not count - 1.
    `output_count` may explicitly request a shorter prefix when reproducing
    historical captures that retained only an initial descriptor window.
    Descriptor bit 0x10 consumes a second descriptor and emits its decoded
    value to t1; a zero second descriptor selects the width-16 form.
    """
    if len(header) != 16:
        raise ValueError("header must cover asset +0x04..+0x13")
    flags = _u16(header, 0)
    if flags & 0x20:
        raise ValueError("asset flag 0x20 path is outside this capture")
    fraction = struct.unpack(">f", fraction_bits.to_bytes(4, "big"))[0]
    scaled = struct.unpack(">f", struct.pack(">f", fraction * 1024.0))[0]
    scaled_bits = struct.unpack(">I", struct.pack(">f", scaled))[0]
    # CVT.W.S follows FCSR; the mode is explicit because the evaluator does
    # not set it here. The captured sample yields 597 in three of four modes.
    weight = _cvt_w_s(scaled, rounding_mode)
    if weight < 0:
        raise ValueError("negative interpolation weight is outside this reader path")

    bit_offset = 0
    fpr = []
    for i in range(3):
        width = header[10 + i]  # asset +0x0e..+0x10
        base = _s16(header, 4 + i * 2)  # asset +0x08..+0x0c
        a = _packed(first, bit_offset, width)
        b = _packed(second, bit_offset, width)
        fixed = _s32((base << 10) + (a << 10) + (b - a) * weight)
        fpr.append(struct.unpack(">I", struct.pack(">f", float(fixed)))[0])
        bit_offset += width

    total_outputs = 3 * (header[13] - 1)
    if output_count is None:
        output_count = total_outputs
    if not isinstance(output_count, int) or not 0 <= output_count <= total_outputs:
        raise ValueError("output_count must be between zero and 3*(count-1)")
    plan, descriptor_bytes_consumed, descriptor_bits = _descriptor_plan(
        descriptors, output_count)
    steps = []
    t5 = bytearray()
    t1 = bytearray()
    for index, pos, desc, width, secondary, secondary_width in plan:
        a = _packed(first, bit_offset, width)
        b = _packed(second, bit_offset, width)
        delta = (b - a) & 0x7FF
        if delta & 0x400:
            delta -= 0x800
        # 80619E04..80619E30: signed 11-bit delta, MIPS mult/mflo,
        # arithmetic >>10, <<5, 16-bit sh. Python's >> is arithmetic.
        scaled_delta = _s32(delta * weight) >> 10
        output = ((desc & 0xFFF0) + ((a + scaled_delta) << 5)) & 0xFFFF
        t5 += output.to_bytes(2, "big")
        secondary_first = secondary_second = secondary_output = 0
        if secondary is not None:
            if secondary_width:
                secondary_offset = bit_offset + width
                secondary_first = _packed(first, secondary_offset, secondary_width)
                secondary_second = _packed(second, secondary_offset, secondary_width)
                # MIPS SUB produces the 32-bit difference; values are unsigned
                # fields no wider than 16 bits, so subtraction cannot overflow.
                difference = _s32(secondary_second - secondary_first)
                product = _s32(difference * weight)  # MULT/MFLO low word
                # This path uses SRL (logical), unlike the primary SRA path.
                interpolated = ((secondary_first << 4) +
                                ((product & 0xFFFFFFFF) >> 6)) & 0xFFFFFFFF
            else:
                interpolated = 0
            secondary_base = 0 if secondary == 0 else secondary ^ secondary_width
            secondary_output = (secondary_base + interpolated) & 0xFFFF
            t1 += secondary_output.to_bytes(2, "big")
        else:
            # 80619E34 masks the already-stored primary word to flag bit 0x10;
            # the normal branch writes that zero to t1.
            t1 += b"\x00\x00"
        steps.append(DescriptorStep(index, pos, desc, bit_offset, width,
                                    a, b, delta, output, secondary,
                                    secondary_width, secondary_first,
                                    secondary_second, secondary_output))
        bit_offset += width
        if secondary is not None:
            bit_offset += secondary_width
    return ReaderResult(fraction, scaled_bits, weight, tuple(fpr),
                        bytes(t5), bytes(t1), tuple(steps),
                        descriptor_bytes_consumed,
                        sum(header[10:13]) + descriptor_bits)


def reproduce_asset(asset: bytes, cursor0: int, cursor1: int,
                    fraction_bits: int, *, output_count: int | None = None,
                    rounding_mode: str = "nearest_even"
                    ) -> tuple[ReaderResult, dict[str, int]]:
    """Reproduce a loaded table-11 asset after the loader's +6 offset fixup.

    `asset` is the decompressed pointer-table entry before
    `func_global_asm_80614130` mutates its signed +0x06 offset by +6.
    The returned metadata describes exact descriptor/sample ranges used.
    """
    if len(asset) < 0x14:
        raise ValueError("table-11 asset is shorter than its reader header")
    if not -0x8000 <= cursor0 <= 0x7FFF or not -0x8000 <= cursor1 <= 0x7FFF:
        raise ValueError("cursor must fit the signed 16-bit runtime field")
    header = bytearray(asset[4:0x14])
    raw_offset = int.from_bytes(header[2:4], "big", signed=True)
    runtime_offset_bits = (raw_offset + 6) & 0xFFFF
    header[2:4] = runtime_offset_bits.to_bytes(2, "big")
    runtime_offset = int.from_bytes(header[2:4], "big", signed=True)
    stride = asset[0x13]
    starts = (runtime_offset + cursor0 * stride,
              runtime_offset + cursor1 * stride)
    if min(starts) < 0 or max(starts) > len(asset):
        raise ValueError("cursor-selected sample starts outside table-11 asset")
    # The decoder's exact bit requirement is derived from the header and full
    # descriptor plan; passing the remaining bounded asset lets _packed detect
    # a short stream without imposing an unproven stride-equality assumption.
    result = reproduce(bytes(header), asset[0x14:], asset[starts[0]:],
                       asset[starts[1]:], fraction_bits,
                       output_count=output_count,
                       rounding_mode=rounding_mode)
    sample_bytes = (result.sample_bits_consumed + 7) // 8
    for start in starts:
        if start + sample_bytes > len(asset):
            raise ValueError("cursor-selected packed sample stream exceeds asset bounds")
    metadata = {
        "raw_offset": raw_offset,
        "runtime_offset": runtime_offset,
        "stride": stride,
        "cursor0_start": starts[0],
        "cursor1_start": starts[1],
        "descriptor_start": 0x14,
        "descriptor_end": 0x14 + result.descriptor_bytes_consumed,
        "descriptor_bytes": result.descriptor_bytes_consumed,
        "sample_bits": result.sample_bits_consumed,
        "sample_bytes": sample_bytes,
        "cursor0_end": starts[0] + sample_bytes,
        "cursor1_end": starts[1] + sample_bytes,
    }
    return result, metadata
