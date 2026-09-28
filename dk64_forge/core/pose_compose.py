"""Compose the direct DK bone matrices through the canonical parent table.

This models only the post-local-matrix hierarchy stage. Matrix words use the
12 meaningful binary32 components of DK64's row-major affine MtxF (three
basis rows followed by translation). DK64 uses row vectors, so hierarchy
composition is local * parent, matching code_1E2D0.s and libultra.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

from .pose_reconstruct import DirectBonePose


def _f32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", value))[0]


def _from_bits(word: int) -> float:
    return struct.unpack(">f", struct.pack(">I", word & 0xFFFFFFFF))[0]


def _bits(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", _f32(value)))[0]


def identity_root_words() -> tuple[int, ...]:
    """Return an explicit affine identity root in compact MtxF word order."""
    return (
        0x3F800000, 0, 0,
        0, 0x3F800000, 0,
        0, 0, 0x3F800000,
        0, 0, 0,
    )


def _matrix_multiply_local_parent_words(
    local: tuple[int, ...], parent: tuple[int, ...]
) -> tuple[int, ...]:
    """MIPS-order binary32 product for row-major affine local * parent."""
    if len(parent) != 12 or len(local) != 12:
        raise ValueError("affine matrices require twelve meaningful words")
    p = tuple(_from_bits(word) for word in parent)
    l = tuple(_from_bits(word) for word in local)
    out: list[int] = []

    # Each compact row stores columns 0,1,2; translation is the fourth row.
    for row in range(3):
        for column in range(3):
            products = (
                _f32(l[row * 3] * p[column]),
                _f32(l[row * 3 + 1] * p[3 + column]),
                _f32(l[row * 3 + 2] * p[6 + column]),
            )
            value = _f32(products[0] + products[1])
            value = _f32(value + products[2])
            out.append(_bits(value))

    for column in range(3):
        products = (
            _f32(l[9] * p[column]),
            _f32(l[10] * p[3 + column]),
            _f32(l[11] * p[6 + column]),
        )
        value = _f32(products[0] + products[1])
        value = _f32(value + products[2])
        value = _f32(value + p[9 + column])
        out.append(_bits(value))
    return tuple(out)


@dataclass(frozen=True)
class ComposedBone:
    local_index: int
    parent_index: int | None
    matrix_words: tuple[int, ...]


def compose_direct_hierarchy(
    local_poses: tuple[DirectBonePose, ...],
    root_transform_words: tuple[int, ...],
) -> tuple[ComposedBone, ...]:
    """Compose one ordered canonical skeleton using an explicit root matrix.

    Pass ``identity_root_words()`` to request a DERIVED MODEL-LOCAL hierarchy;
    identity is never selected implicitly. Input poses must be in canonical
    local-index order, with one root at index 0 and all parents earlier than
    their children. The caller supplies local matrices after any required
    pre-composition per-actor scale/translation adjustments.
    """
    count = len(local_poses)
    if count == 0 or len(root_transform_words) != 12:
        raise ValueError("a skeleton and explicit twelve-word root are required")
    if tuple(p.local_index for p in local_poses) != tuple(range(count)):
        raise ValueError("local poses must be in contiguous local-index order")
    roots = [p.local_index for p in local_poses if p.parent_index == 0xFF]
    if roots != [0]:
        raise ValueError("canonical composition requires only local index 0 as root")

    composed: dict[int, tuple[int, ...]] = {}
    result: list[ComposedBone] = []
    for pose in local_poses:
        if len(pose.matrix_words) != 12:
            raise ValueError(f"bone {pose.local_index} has an incomplete local matrix")
        if pose.local_index == 0:
            global_words = _matrix_multiply_local_parent_words(
                pose.matrix_words, root_transform_words)
        else:
            parent = pose.parent_index
            if parent is None or parent == 0xFF or parent >= pose.local_index:
                raise ValueError(f"bone {pose.local_index} parent is invalid or not yet composed")
            if parent not in composed:
                raise ValueError(f"bone {pose.local_index} parent {parent} is missing")
            global_words = _matrix_multiply_local_parent_words(
                pose.matrix_words, composed[parent])
        composed[pose.local_index] = global_words
        result.append(ComposedBone(pose.local_index, pose.parent_index, global_words))
    return tuple(result)
