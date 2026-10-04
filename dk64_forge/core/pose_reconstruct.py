"""Reconstruct a complete direct-path DK local pose from one coherent sample.

Inputs are the 25 actor bone records, 75 pre-adjust t5 and t1 halfwords, and
the full terminated adjustment list captured for the same evaluation. No
runtime capture is embedded here; the module returns raw matrix words so
signed zero and every other float32 bit pattern are preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import struct

from .adjustment import apply_master_adjustments, parse_adjustment_records
from .bone_matrix import MATRIX_OFFSETS, bone_local_matrix_bits


@dataclass(frozen=True)
class DirectBonePose:
    record_index: int
    parent_index: int
    local_index: int
    master_index: int
    pre_t5: tuple[int, int, int]
    pre_t1: tuple[int, int, int]
    adjustment_count: int
    post_t5: tuple[int, int, int]
    matrix_words: tuple[int, ...]

    def report(self) -> dict[str, object]:
        return {
            "record_index": self.record_index,
            "parent_index": self.parent_index,
            "local_index": self.local_index,
            "master_index": self.master_index,
            "pre_adjust_t5": [f"{v:04X}" for v in self.pre_t5],
            "pre_adjust_t1": [f"{v:04X}" for v in self.pre_t1],
            "adjustment_count": self.adjustment_count,
            "post_adjust_t5": [f"{v:04X}" for v in self.post_t5],
            "matrix_words": {
                f"+{offset:02X}": f"{word:08X}"
                for offset, word in zip(MATRIX_OFFSETS, self.matrix_words)
            },
        }


@dataclass(frozen=True)
class DirectPoseCapture:
    identity: dict[str, object]
    route: dict[str, object]
    t5: tuple[int, ...]
    t1: tuple[int, ...]
    adjustment_records: bytes
    bone_records: bytes


def parse_capture_json(text: str) -> DirectPoseCapture:
    """Parse the one-line JSON block printed by the CE full-scratch probe."""
    marker = "[DK64 direct pose JSON] "
    line = next((row[len(marker):] for row in text.splitlines()
                 if row.startswith(marker)), text.strip())
    document = json.loads(line)
    if document.get("schema") != "dk64-direct-pose-capture-v1":
        raise ValueError("unrecognized direct-pose capture schema")
    count = document.get("bone_count")
    if count != 25:
        raise ValueError("capture is not the canonical 25-bone count")
    def exact_hex(field: str, expected_bytes: int | None) -> bytes:
        value = document[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]*", value) is None:
            raise ValueError(f"{field} must be uninterrupted hexadecimal")
        if expected_bytes is not None and len(value) != expected_bytes * 2:
            raise ValueError(f"{field} must contain exactly {expected_bytes} bytes as hex")
        try:
            return bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError(f"{field} contains invalid hexadecimal") from exc

    t5_bytes = exact_hex("t5_hex", 150)
    t1_bytes = exact_hex("t1_hex", 150)
    adjustment_records = exact_hex("adjustments_hex", None)
    bone_records = exact_hex("bone_records_hex", 25 * 0x10)
    # Require a complete, valid zero-marker-terminated list before exposing it.
    parsed_adjustments = parse_adjustment_records(adjustment_records)
    if len(adjustment_records) == 0 or len(adjustment_records) % 8 or \
            len(parsed_adjustments) * 8 != len(adjustment_records) or not parsed_adjustments[-1].terminator:
        raise ValueError("adjustment bytes must end exactly at a complete zero-weight terminator")
    if "adjustment_rows" in document and document["adjustment_rows"] != len(parsed_adjustments) - 1:
        raise ValueError("capture adjustment_rows does not match the active records")
    identity_keys = ("actor", "aas", "state", "state_off", "id", "asset",
                     "cursor0", "cursor1", "fraction_bits", "adjustment")
    route_keys = ("unk4_asset", "unk8_asset", "primary_flags", "gate28")
    identity = {key: document[key] for key in identity_keys}
    route = {key: document[key] for key in route_keys}
    return DirectPoseCapture(
        identity=identity,
        route=route,
        t5=struct.unpack(">75H", t5_bytes),
        t1=struct.unpack(">75H", t1_bytes),
        adjustment_records=adjustment_records,
        bone_records=bone_records,
    )


def reconstruct_direct_local_pose(
    bone_records: bytes,
    pre_t5: tuple[int, ...],
    pre_t1: tuple[int, ...],
    adjustment_records: bytes,
    quarter_table: tuple[int, ...],
    expected_bones: int = 25,
) -> tuple[DirectBonePose, ...]:
    """Build local direct matrices, ordered by destination local index.

    The caller must provide one coherent evaluator sample. Scratch values are
    unsigned u16 bit patterns in master-major XYZ triplets. Bone records are
    0x10-byte big-endian records; translation floats are preserved as raw
    binary32 words.
    """
    if len(bone_records) != expected_bones * 0x10:
        raise ValueError(f"expected {expected_bones} complete bone records")
    if len(pre_t5) != expected_bones * 3 or len(pre_t1) != expected_bones * 3:
        raise ValueError(f"expected {expected_bones * 3} t5 and t1 halfwords")

    adjusted = apply_master_adjustments(adjustment_records, pre_t5, pre_t1)
    return _build_direct_local_pose(
        bone_records, pre_t5, pre_t1, adjusted.t5, adjusted.t1,
        adjusted.selector_counts, quarter_table, expected_bones,
    )


def reconstruct_direct_local_pose_unadjusted(
    bone_records: bytes,
    t5: tuple[int, ...],
    t1: tuple[int, ...],
    quarter_table: tuple[int, ...],
    expected_bones: int = 25,
) -> tuple[DirectBonePose, ...]:
    """Build experimental direct matrices without reading adjustment records."""
    channels = len(t5) // 3
    if len(t5) != channels * 3 or len(t1) != len(t5) or channels < expected_bones:
        raise ValueError(f"expected complete t5/t1 channel triples for {expected_bones} bones")
    return _build_direct_local_pose(
        bone_records, t5, t1, t5, t1, (0,) * channels,
        quarter_table, expected_bones,
    )


def _build_direct_local_pose(
    bone_records: bytes,
    pre_t5: tuple[int, ...],
    pre_t1: tuple[int, ...],
    post_t5: tuple[int, ...],
    post_t1: tuple[int, ...],
    selector_counts: tuple[int, ...],
    quarter_table: tuple[int, ...],
    expected_bones: int,
) -> tuple[DirectBonePose, ...]:
    if len(bone_records) != expected_bones * 0x10:
        raise ValueError(f"expected {expected_bones} complete bone records")
    local_slots: dict[int, DirectBonePose] = {}
    masters: set[int] = set()
    for row in range(expected_bones):
        record = bone_records[row * 0x10:(row + 1) * 0x10]
        local_index, master_index = record[1], record[2]
        if local_index >= expected_bones or master_index >= len(post_t5) // 3:
            raise ValueError(f"bone record {row} has out-of-range local/master index")
        if local_index in local_slots:
            raise ValueError(f"duplicate local matrix index {local_index}")
        if master_index in masters:
            raise ValueError(f"duplicate master scratch index {master_index}")
        masters.add(master_index)

        begin = master_index * 3
        angles = post_t5[begin:begin + 3]
        scales = post_t1[begin:begin + 3]
        translation_bits = struct.unpack_from(">III", record, 4)
        local_slots[local_index] = DirectBonePose(
            record_index=row,
            parent_index=record[0],
            local_index=local_index,
            master_index=master_index,
            pre_t5=tuple(v & 0xFFFF for v in pre_t5[begin:begin + 3]),
            pre_t1=tuple(v & 0xFFFF for v in pre_t1[begin:begin + 3]),
            adjustment_count=selector_counts[master_index],
            post_t5=tuple(v & 0xFFFF for v in angles),
            matrix_words=bone_local_matrix_bits(angles, translation_bits,
                                                scales, quarter_table),
        )
    if set(local_slots) != set(range(expected_bones)) or (len(post_t5) == expected_bones * 3 and masters != set(range(expected_bones))):
        raise ValueError("local/master indices do not cover each expected slot exactly once")
    return tuple(local_slots[index] for index in range(expected_bones))


def trace_report(poses: tuple[DirectBonePose, ...]) -> dict[str, object]:
    """Return a JSON-ready direct-local-pose trace, not composed transforms."""
    return {
        "stage": "direct local matrices before later scale/parent passes",
        "bone_count": len(poses),
        "bones": [pose.report() for pose in poses],
    }


def reconstruct_capture_text(text: str, quarter_table: tuple[int, ...]) -> dict[str, object]:
    """Import one CE dump block and immediately produce its 25-row pose trace."""
    capture = parse_capture_json(text)
    poses = reconstruct_direct_local_pose(
        capture.bone_records, capture.t5, capture.t1,
        capture.adjustment_records, quarter_table,
    )
    report = trace_report(poses)
    report["capture_identity"] = capture.identity
    report["route"] = capture.route
    adjusted = apply_master_adjustments(capture.adjustment_records, capture.t5, capture.t1)
    report["adjusted_t5_hex"] = [f"{value:04X}" for value in adjusted.t5]
    report["post_adjust_t1_hex"] = [f"{value:04X}" for value in adjusted.t1]
    report["master_groups"] = [
        {
            "master": master,
            "pre_t5_hex": [f"{value:04X}" for value in capture.t5[master * 3:master * 3 + 3]],
            "pre_t5_signed": [_signed16(value) for value in capture.t5[master * 3:master * 3 + 3]],
            "pre_t1_hex": [f"{value:04X}" for value in capture.t1[master * 3:master * 3 + 3]],
            "pre_t1_signed": [_signed16(value) for value in capture.t1[master * 3:master * 3 + 3]],
            "post_t5_hex": [f"{value:04X}" for value in adjusted.t5[master * 3:master * 3 + 3]],
            "post_t5_signed": [_signed16(value) for value in adjusted.t5[master * 3:master * 3 + 3]],
        }
        for master in range(25)
    ]
    rows = parse_adjustment_records(capture.adjustment_records)
    adjustment_trace = []
    current_t5 = tuple(capture.t5)
    for row in rows:
        if row.terminator:
            break
        before = current_t5
        prefix = capture.adjustment_records[:(row.index + 1) * 8] + bytes(8)
        cumulative = apply_master_adjustments(prefix, capture.t5, capture.t1)
        current_t5 = cumulative.t5
        start = row.selector * 3
        adjustment_trace.append({
            "index": row.index, "selector": row.selector, "weight": row.weight,
            "targets_signed": list(row.targets),
            "targets_hex": [f"{value & 0xFFFF:04X}" for value in row.targets],
            "before_t5_hex": [f"{value & 0xFFFF:04X}" for value in before[start:start + 3]],
            "before_t5_signed": [_signed16(value) for value in before[start:start + 3]],
            "after_t5_hex": [f"{value:04X}" for value in cumulative.t5[start:start + 3]],
            "after_t5_signed": [_signed16(value) for value in cumulative.t5[start:start + 3]],
        })
    report["adjustment_trace"] = adjustment_trace
    return report


def _signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value
