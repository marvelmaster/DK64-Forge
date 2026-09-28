"""One observed DK64 animation-channel mapping, before global composition.

The 0x8061A2A8 evaluator path is conditional. This helper deliberately covers
only the captured bone_01 triplet and its local matrix +0 value.
"""

import re
import struct
import zlib
from pathlib import Path

# The quarter-wave table (decomp symbol D_global_asm_8061ACA0) lives in the game's
# global_asm code overlay, so it can be read from the ROM without the decomp sources.
CODE_OVERLAY_ROM = 0x113F0        # gzip stream in the normalized ROM
CODE_OVERLAY_VRAM = 0x805FB300
QUARTER_TABLE_VRAM = 0x8061ACA0
QUARTER_TABLE_WORDS = 516
_ROM_TABLE_CACHE: dict[tuple, tuple[int, ...]] = {}


def selected_triplet(bone_record: bytes, scratch_t5: bytes) -> tuple[int, tuple[int, int, int]]:
    if len(bone_record) != 16:
        raise ValueError("expected a 16-byte actor bone record")
    local_index, master_index = bone_record[1:3]
    start = master_index * 6  # 8061A304..8061A320
    if start + 6 > len(scratch_t5):
        raise ValueError("selected triplet is outside captured scratch prefix")
    values = struct.unpack_from(">HHH", scratch_t5, start)
    return local_index, values


def local_matrix_destination(bone_record: bytes, matrix_array: int, stride: int = 0x40) -> int:
    """Return the matrix slot selected by bone-record byte +1 (local index)."""
    if len(bone_record) != 16:
        raise ValueError("expected a 16-byte actor bone record")
    return matrix_array + bone_record[1] * stride


def quarter_table_words_from_rom(rom: bytes) -> tuple[int, ...]:
    """The 516-word quarter-wave table, read from a normalized (big-endian) ROM.

    Verified identical to the table in the decomp's code_1E2D0.s for DK64 US rev 0.
    """
    key = (len(rom), bytes(rom[CODE_OVERLAY_ROM:CODE_OVERLAY_ROM + 32]))
    if key not in _ROM_TABLE_CACHE:
        code = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(
            bytes(rom[CODE_OVERLAY_ROM:CODE_OVERLAY_ROM + 0x200000]))
        offset = QUARTER_TABLE_VRAM - CODE_OVERLAY_VRAM
        if offset + 4 * QUARTER_TABLE_WORDS > len(code):
            raise ValueError("unexpected DK64 code overlay size")
        _ROM_TABLE_CACHE[key] = struct.unpack_from(f">{QUARTER_TABLE_WORDS}I", code, offset)
    return _ROM_TABLE_CACHE[key]


def source_quarter_table_words(assembly_path: Path) -> tuple[int, ...]:
    text = assembly_path.read_text(encoding="utf-8")
    lines = text.split("glabel D_global_asm_8061ACA0", 1)[1].splitlines()[1:]
    values = []
    for line in lines:
        match = re.search(r"\.word 0x([0-9A-Fa-f]{8})", line)
        if not match:
            break
        values.append(int(match.group(1), 16))
    if len(values) != 516:
        raise ValueError("unexpected DK64 quarter-wave table size")
    return tuple(values)


def bone01_local_m00_bits(angles: tuple[int, int, int], table: tuple[int, ...]) -> int:
    """Return direct-loop bone_01 M00 bits from the assembly lookup path.

    ``angles`` are the raw X/Z/Y halfwords selected at 0x8061A31C..A424.
    The first matrix component is pair(Y).f0 * pair(Z).f0. This is the
    pre-hierarchy direct-loop result; later actor scale/composition is outside
    this helper.
    """
    return bone01_local_m00_scaled_bits(angles, (0, 0, 0), table)


def _float32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", value))[0]


def _angle_pair_f0(angle: int, table: tuple[int, ...]) -> float:
    """Transcribe f0 from the four quadrant branches at 0x8061A328..A39C."""
    return _angle_pair(angle, table)[0]


def _angle_pair(angle: int, table: tuple[int, ...]) -> tuple[float, float]:
    """Return the source's two table-derived terms for one halfword angle."""
    if len(table) != 516:
        raise ValueError("expected the source quarter-wave table")
    phase = ((angle & 0xFFFF) >> 5) & 0x7FF
    quadrant = phase & 0x600
    index = phase & 0x1FF
    low = struct.unpack(">f", struct.pack(">I", table[index]))[0]
    high = struct.unpack(">f", struct.pack(">I", table[512 - index]))[0]
    if quadrant == 0x000:
        return high, low
    if quadrant == 0x200:
        return _float32(-low), high
    if quadrant == 0x400:
        return _float32(-high), _float32(-low)
    return low, _float32(-high)


def bone01_local_m00_scaled_bits(
    angles: tuple[int, int, int],
    t1: tuple[int, int, int],
    table: tuple[int, ...],
) -> int:
    """Predict direct-loop M00, including the source's optional t1[3] scale.

    The direct loop uses unsigned ``lhu`` for the t1 scale word and multiplies
    M00 by ``word * 2^-15`` only when that word is nonzero.
    """
    if len(angles) != 3 or len(t1) != 3:
        raise ValueError("angles and t1 must each contain three halfwords")
    _x, z, y = angles
    m00 = _float32(_angle_pair_f0(y, table) * _angle_pair_f0(z, table))
    scale_word = t1[0] & 0xFFFF
    if scale_word:
        scale = _float32(_float32(float(scale_word)) * _float32(1.0 / 32768.0))
        m00 = _float32(m00 * scale)
    return struct.unpack(">I", struct.pack(">f", m00))[0]


MATRIX_OFFSETS = (0x00, 0x04, 0x08, 0x10, 0x14, 0x18,
                  0x20, 0x24, 0x28, 0x30, 0x34, 0x38)


def _bits(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", value))[0]


def bone_local_matrix_bits(
    angles: tuple[int, int, int],
    translation_bits: tuple[int, int, int],
    scales: tuple[int, int, int],
    table: tuple[int, ...],
) -> tuple[int, ...]:
    """Predict the direct loop's twelve stored matrix words for one bone.

    ``angles`` are scratch halfwords in source order X, Z, Y; translation
    words are the bone record's raw IEEE-754 fields at +4/+8/+12. ``scales``
    are the unsigned ``lhu`` words selected from t1 at the bone's master slot.
    Multiplication/addition/subtraction each round to binary32 as MIPS
    ``.s`` instructions do. This is the direct local matrix only, before the
    later scale-array pass or parent composition.
    """
    if len(angles) != 3 or len(translation_bits) != 3 or len(scales) != 3:
        raise ValueError("angles, translation_bits, and scales must each have three values")
    # Assembly loads X from +0, Y from +4, Z from +2; its pair registers are
    # named (f0,f1), (f4,f5), and (f2,f3), respectively.
    f0, f1 = _angle_pair(angles[0], table)
    f4, f5 = _angle_pair(angles[2], table)
    f2, f3 = _angle_pair(angles[1], table)
    f6 = _float32(f1 * f5)
    f7 = _float32(f0 * f5)
    f8 = _float32(f1 * f4)
    f9 = _float32(f0 * f4)

    # Match the source instruction sequence at 0x8061A4C0..0x8061A618.
    m00 = _float32(f2 * f4)
    m01 = _float32(f2 * f5)
    m02 = _float32(0.0 - f3)
    m10 = _float32(_float32(f8 * f3) - f7)
    m11 = _float32(_float32(f6 * f3) + f9)
    m12 = _float32(f1 * f2)
    m20 = _float32(_float32(f9 * f3) + f6)
    m21 = _float32(_float32(f7 * f3) - f8)
    m22 = _float32(f0 * f2)

    rot = [m00, m01, m02, m10, m11, m12, m20, m21, m22]
    for row, word in enumerate(scales):
        scale_word = word & 0xFFFF
        if scale_word:
            factor = _float32(_float32(float(scale_word)) * _float32(1.0 / 32768.0))
            for col in range(3):
                i = row * 3 + col
                rot[i] = _float32(rot[i] * factor)
    values = tuple(_bits(value) for value in rot) + tuple(v & 0xFFFFFFFF for v in translation_bits)
    return values
