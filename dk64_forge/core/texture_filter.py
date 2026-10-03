"""Texture resampling for previews: nearest (pixel view) or trilinear (mipmapped).

Trilinear filtering samples a mip chain (2x2 box-filtered levels): within each of the two
levels around the scale factor it interpolates bilinearly between texel centres, then
blends the two levels by the fractional level of detail, as GPUs do with
GL_LINEAR_MIPMAP_LINEAR. Edges clamp. This is a desktop preview filter; the N64 RDP itself
uses three-point bilinear filtering and its own LOD rules.
"""

from __future__ import annotations

import math

import numpy as np


def mip_chain(rgba: np.ndarray) -> list[np.ndarray]:
    """Levels of a float (h, w, 4) image down to 1x1, each a 2x2 average of the previous."""
    levels = [rgba.astype(np.float32)]
    while levels[-1].shape[0] > 1 or levels[-1].shape[1] > 1:
        level = levels[-1]
        h, w = level.shape[:2]
        if h % 2:
            level = np.concatenate([level, level[-1:]], axis=0)
        if w % 2:
            level = np.concatenate([level, level[:, -1:]], axis=1)
        h2, w2 = level.shape[0] // 2, level.shape[1] // 2
        levels.append(level.reshape(h2, 2, w2, 2, 4).mean(axis=(1, 3)))
    return levels


def _bilinear(level: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    h, w = level.shape[:2]
    x = (np.arange(out_w) + 0.5) * (w / out_w) - 0.5
    y = (np.arange(out_h) + 0.5) * (h / out_h) - 0.5
    # Clamp each tap separately: clipping the lower tap before adding one
    # incorrectly blends the second texel into the image's top/left border.
    x_floor = np.floor(x).astype(int)
    y_floor = np.floor(y).astype(int)
    x0, x1 = np.clip(x_floor, 0, w - 1), np.clip(x_floor + 1, 0, w - 1)
    y0, y1 = np.clip(y_floor, 0, h - 1), np.clip(y_floor + 1, 0, h - 1)
    fx = np.clip(x - np.floor(x), 0, 1)[None, :, None]
    fy = np.clip(y - np.floor(y), 0, 1)[:, None, None]
    top = level[y0][:, x0] * (1 - fx) + level[y0][:, x1] * fx
    bottom = level[y1][:, x0] * (1 - fx) + level[y1][:, x1] * fx
    return top * (1 - fy) + bottom * fy


def resample(rgba: bytes, width: int, height: int, out_w: int, out_h: int,
             mode: str = "nearest") -> bytes:
    """RGBA8888 image of size out_w x out_h ('nearest' or 'trilinear')."""
    image = np.frombuffer(rgba, dtype=np.uint8, count=width * height * 4).reshape(height, width, 4)
    if mode == "nearest":
        xs = np.minimum((np.arange(out_w) * width) // out_w, width - 1)
        ys = np.minimum((np.arange(out_h) * height) // out_h, height - 1)
        return np.ascontiguousarray(image[ys][:, xs]).tobytes()
    if mode != "trilinear":
        raise ValueError(f"unknown filter mode {mode!r}")
    levels = mip_chain(image)
    scale = max(width / out_w, height / out_h)
    lod = max(0.0, math.log2(scale)) if scale > 0 else 0.0
    low = min(int(math.floor(lod)), len(levels) - 1)
    high = min(low + 1, len(levels) - 1)
    t = lod - math.floor(lod) if high != low else 0.0
    result = _bilinear(levels[low], out_w, out_h)
    if t > 0:
        result = result * (1 - t) + _bilinear(levels[high], out_w, out_h) * t
    return np.clip(np.rint(result), 0, 255).astype(np.uint8).tobytes()
