"""Spherical G_TEXTURE_GEN coordinates for canonical DK (Phase 1G).

Evidence: research/PHASE1_STATIC_DK.md, Phase 1G.
- libultra guLookAtHiliteF (upstream/dk64_decomp/src/dk64_boot/gu/lookathil.c):
  the LookAt directions are the camera right and up axes; DK64 builds them from
  the player camera (code_450.c:617) and loads them with gSPLookAt
  (code_2F550.c:895). The view matrix is on the projection stack, so the
  modelview holds only actor/bone transforms and normals are dotted in world space.
- RT64 TextureGen.hlsli / rt64_rsp.cpp: d = clamp(N . lookAt, -1, 1),
  spherical S = scale/65536 * (d + 1) * 512 texels, linear S = scale/65536 *
  acos(-d) * 1024/pi texels.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

Vec3 = tuple[float, float, float]
TEXGEN_RANGE = 512.0          # (d + 1) * 512 spans the 0.16 scale's 1024 units
TEXGEN_LINEAR_SCALE = 325.94932  # 1024 / pi, RT64 TextureGen.hlsli


def _normalize(v: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(v))
    if length == 0.0:
        raise ValueError("zero-length camera vector")
    return v / length


def lookat_basis(eye: Sequence[float], at: Sequence[float],
                 up: Sequence[float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """guLookAtHiliteF basis: (right, up, look) with look pointing at the viewer."""
    look = -_normalize(np.asarray(at, dtype=np.float64) - np.asarray(eye, dtype=np.float64))
    right = _normalize(np.cross(np.asarray(up, dtype=np.float64), look))
    new_up = _normalize(np.cross(look, right))
    return right, new_up, look


def texgen_texels(normals: np.ndarray, right: np.ndarray, up: np.ndarray,
                  scale_s: int, scale_t: int, linear: bool = False) -> np.ndarray:
    """RSP-generated S/T in texels for world-space normals of shape (n, 3)."""
    d = np.clip(np.stack((normals @ right, normals @ up), axis=-1), -1.0, 1.0)
    base = np.arccos(-d) * TEXGEN_LINEAR_SCALE if linear else (d + 1.0) * TEXGEN_RANGE
    return base * (np.array((scale_s, scale_t), dtype=np.float64) / 65536.0)


def hilite_upper_left(eye: Sequence[float], at: Sequence[float], up: Sequence[float],
                      light: Sequence[float], width: int = 32,
                      height: int = 32) -> tuple[int, int]:
    """guLookAtHiliteF h.x1/h.y1 as used by gDPSetHilite1Tile (quarter texels)."""
    right, new_up, look = lookat_basis(eye, at, up)
    hilite = _normalize(np.asarray(light, dtype=np.float64)) + look
    length = float(np.linalg.norm(hilite))
    if length <= 0.1:  # THRESH2
        return width * 2, height * 2
    hilite /= length
    # Hilite fields are s32; the C float-to-int assignment truncates toward zero.
    x1 = math.trunc(width * 4 + float(hilite @ right) * width * 2)
    y1 = math.trunc(height * 4 + float(hilite @ new_up) * height * 2)
    return x1 & 0xFFF, y1 & 0xFFF
