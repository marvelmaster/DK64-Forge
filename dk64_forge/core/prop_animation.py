"""Embedded model-two matrix tracks (decomp code_54150, 8064F450..806500E0).

Track selection, looping and speed are preview controls: object scripts normally
choose these. Matrices use the source's pre/post transforms and Z/Y/X rotations.
"""
from dataclasses import dataclass
import math
import struct
import numpy as np


@dataclass(frozen=True)
class PropTrack:
    index: int
    speeds: tuple[int, ...]
    rows: tuple

    def cursor(self, tick: int, speed: int = 1):
        key, fraction = 0, 0.0
        # 806500E0 advances by interpolated speed / 300, times script speed.
        # Preview loops forward; the duplicate terminal key is not a segment.
        for _ in range(max(0, min(int(tick), 100000))):
            rate = self.speeds[key] + (self.speeds[key + 1] - self.speeds[key]) * fraction
            fraction += rate * speed / 300.0
            advance = int(fraction)
            fraction -= advance
            key = (key + advance) % (len(self.speeds) - 1)
        return key, fraction


@dataclass(frozen=True)
class PropRig:
    matrices: dict
    pairs: dict
    tracks: tuple[PropTrack, ...]

    def pose(self, track: int | None = None, tick: int = 0, speed: int = 1):
        result = dict(self.matrices)
        selected = next((t for t in self.tracks if t.index == track), None)
        if selected is None:
            return result
        key, fraction = selected.cursor(tick, speed)
        for destination, pair, samples in selected.rows:
            a, b = np.asarray(samples[key]), np.asarray(samples[key + 1])
            values = a + (b - a) * fraction
            values[3:6] = a[3:6] + ((b[3:6] - a[3:6] + 180) % 360 - 180) * fraction
            scale = np.diag((*values[:3], 1.0))
            x, y, z = np.radians(values[3:6])
            cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
            rx = np.array(((1,0,0,0),(0,cx,-sx,0),(0,sx,cx,0),(0,0,0,1)))
            ry = np.array(((cy,0,sy,0),(0,1,0,0),(-sy,0,cy,0),(0,0,0,1)))
            rz = np.array(((cz,-sz,0,0),(sz,cz,0,0),(0,0,1,0),(0,0,0,1)))
            translation = np.eye(4)
            translation[:3, 3] = values[6:9]
            pre, post = self.pairs[pair]
            result[destination] = post @ scale @ rx @ ry @ rz @ translation @ pre
        return result


def parse(data: bytes) -> PropRig | None:
    if len(data) < 0x6C:
        return None
    animation, buffer = struct.unpack_from('>2I', data, 0x64)
    if not animation or animation == buffer:
        return None
    if not 0 <= animation < buffer <= len(data) - 8 or animation + 40 > buffer:
        raise ValueError('Invalid prop matrix sections')
    total, copied = struct.unpack_from('>2I', data, buffer)
    if not 0 <= copied <= total <= 0x100000 or copied % 64 or total % 64 or buffer + 8 + copied > len(data):
        raise ValueError('Invalid prop matrix buffer')
    pairs, tracks = {}, []
    matrices = {offset: np.eye(4) for offset in range(copied, total, 64)}
    for index in range(10):
        start = animation + struct.unpack_from('>I', data, animation + 4 * index)[0]
        if start >= buffer:
            continue  # unused tracks point at the next section
        if start < animation + 40 or start + 0x3C > buffer:
            raise ValueError('Invalid prop animation offset')
        count, rows = data[start], data[start + 0x39]
        if not count or not rows:
            continue  # empty/unused control slots
        if not 2 <= count <= 56 or not rows or start + 0x3C + rows * (8 + count * 36) > buffer:
            raise ValueError('Invalid prop animation track')
        parsed = []
        for row in range(rows):
            at = start + 0x3C + row * (8 + count * 36)
            destination, pair = struct.unpack_from('>2I', data, at)
            if destination % 64 or destination + 64 > total or pair % 64 or pair + 128 > copied:
                raise ValueError('Prop animation matrix outside buffer')
            if pair not in pairs:
                raw = np.asarray(struct.unpack_from('>32f', data, buffer + 8 + pair)).reshape(2,4,4)
                if not np.isfinite(raw).all():
                    raise ValueError('Non-finite prop base matrix')
                pairs[pair] = raw[0].T.copy(), raw[1].T.copy()
            values = struct.unpack_from(f'>{count * 9}f', data, at + 8)
            if not all(math.isfinite(v) for v in values):
                raise ValueError('Non-finite prop animation key')
            parsed.append((destination, pair, tuple(tuple(values[k*9:k*9+9]) for k in range(count))))
        tracks.append(PropTrack(index, tuple(data[start+1:start+1+count]), tuple(parsed)))
    return PropRig(matrices, pairs, tuple(tracks))
