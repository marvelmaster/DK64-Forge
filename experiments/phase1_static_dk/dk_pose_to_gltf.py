"""Convert DK64 row-vector local matrices into glTF joint-local TRS."""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct


def _float_word(word: int) -> float:
    return struct.unpack(">f", struct.pack(">I", word & 0xFFFFFFFF))[0]


def compact_local_to_gltf_matrix(words: tuple[int, ...]) -> tuple[float, ...]:
    """Transpose DK64's 3x4 row-vector affine transform to column-vector form.

    The returned 4x4 matrix is stored in Python row-major order. glTF's
    parent-first global matrix is then parent * local.
    """
    if len(words) != 12:
        raise ValueError("a DK64 affine transform requires twelve words")
    w = tuple(_float_word(word) for word in words)
    if not all(math.isfinite(value) for value in w):
        raise ValueError("nonfinite DK64 matrix")
    return (w[0], w[3], w[6], w[9],
            w[1], w[4], w[7], w[10],
            w[2], w[5], w[8], w[11],
            0.0, 0.0, 0.0, 1.0)


@dataclass(frozen=True)
class JointTRS:
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: tuple[float, float, float]
    reconstruction_error: float
    orthogonality_error: float


def _rotation_to_quaternion(r: tuple[float, ...]) -> tuple[float, float, float, float]:
    # Row-major 3x3 rotation matrix, output glTF quaternion XYZW.
    trace = r[0] + r[4] + r[8]
    if trace > 0.0:
        root = math.sqrt(trace + 1.0) * 2.0
        x = (r[7] - r[5]) / root
        y = (r[2] - r[6]) / root
        z = (r[3] - r[1]) / root
        w = root / 4.0
    elif r[0] > r[4] and r[0] > r[8]:
        root = math.sqrt(1.0 + r[0] - r[4] - r[8]) * 2.0
        x = root / 4.0
        y = (r[1] + r[3]) / root
        z = (r[2] + r[6]) / root
        w = (r[7] - r[5]) / root
    elif r[4] > r[8]:
        root = math.sqrt(1.0 + r[4] - r[0] - r[8]) * 2.0
        x = (r[1] + r[3]) / root
        y = root / 4.0
        z = (r[5] + r[7]) / root
        w = (r[2] - r[6]) / root
    else:
        root = math.sqrt(1.0 + r[8] - r[0] - r[4]) * 2.0
        x = (r[2] + r[6]) / root
        y = (r[5] + r[7]) / root
        z = root / 4.0
        w = (r[3] - r[1]) / root
    length = math.sqrt(x*x + y*y + z*z + w*w)
    if length < 1e-12 or not math.isfinite(length):
        raise ValueError("invalid matrix rotation quaternion")
    q = (x / length, y / length, z / length, w / length)
    return tuple(-value for value in q) if q[3] < 0.0 else q


def recompose_trs(trs: JointTRS) -> tuple[float, ...]:
    x, y, z, w = trs.rotation
    sx, sy, sz = trs.scale
    tx, ty, tz = trs.translation
    return (
        (1 - 2*(y*y + z*z))*sx, 2*(x*y - z*w)*sy, 2*(x*z + y*w)*sz, tx,
        2*(x*y + z*w)*sx, (1 - 2*(x*x + z*z))*sy, 2*(y*z - x*w)*sz, ty,
        2*(x*z - y*w)*sx, 2*(y*z + x*w)*sy, (1 - 2*(x*x + y*y))*sz, tz,
        0.0, 0.0, 0.0, 1.0,
    )


def decompose_joint_local(
    matrix: tuple[float, ...], *, tolerance: float = 3e-5
) -> JointTRS:
    """Reject non-TRS affine matrices, including meaningful shear."""
    if len(matrix) != 16 or not all(math.isfinite(v) for v in matrix):
        raise ValueError("joint matrix must be finite 4x4")
    if matrix[12:] != (0.0, 0.0, 0.0, 1.0):
        raise ValueError("joint matrix has non-affine bottom row")
    columns = [
        (matrix[0], matrix[4], matrix[8]),
        (matrix[1], matrix[5], matrix[9]),
        (matrix[2], matrix[6], matrix[10]),
    ]
    scales = [math.sqrt(sum(v*v for v in col)) for col in columns]
    if any(not math.isfinite(scale) or scale < 1e-8 for scale in scales):
        raise ValueError("joint matrix has zero or nonfinite scale")
    unit = [tuple(v / scales[i] for v in columns[i]) for i in range(3)]
    dot = lambda a, b: sum(x*y for x, y in zip(a, b))
    orthogonality_error = max(abs(dot(unit[i], unit[j]))
                              for i, j in ((0, 1), (0, 2), (1, 2)))
    cross = (unit[1][1]*unit[2][2] - unit[1][2]*unit[2][1],
             unit[1][2]*unit[2][0] - unit[1][0]*unit[2][2],
             unit[1][0]*unit[2][1] - unit[1][1]*unit[2][0])
    determinant = dot(unit[0], cross)
    if determinant < 0.0:
        scales[0] = -scales[0]
        unit[0] = tuple(-value for value in unit[0])
        determinant = -determinant
    if orthogonality_error > tolerance or abs(determinant - 1.0) > tolerance:
        raise ValueError(f"joint matrix has shear/non-orthogonal basis: "
                         f"dot={orthogonality_error:.8g}, det={determinant:.8g}")
    rotation = (
        unit[0][0], unit[1][0], unit[2][0],
        unit[0][1], unit[1][1], unit[2][1],
        unit[0][2], unit[1][2], unit[2][2],
    )
    trs = JointTRS(
        (matrix[3], matrix[7], matrix[11]),
        _rotation_to_quaternion(rotation),
        tuple(scales), 0.0, orthogonality_error,
    )
    error = max(abs(a-b) for a,b in zip(matrix, recompose_trs(trs)))
    if error > tolerance:
        raise ValueError(f"joint matrix TRS residual {error:.8g} exceeds {tolerance}")
    return JointTRS(trs.translation, trs.rotation, trs.scale,
                    error, orthogonality_error)
