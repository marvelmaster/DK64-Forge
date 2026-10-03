"""ROM texture-frame descriptors, with bounds checks.

Map: code_2F550.c 8062F050/8062EE48, header 0x48, 0x7C-byte rows.
Prop: ROM disassembly 806349FC..80634C90, header 0x6C, 0x84-byte rows;
80639CD0 loads animated images from table 7, static images remain table 25.
"""
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class TextureAnimation:
    key: int
    frames: tuple[int, ...]
    ticks_per_frame: int
    table: int
    group: int = 0
    interpolate: bool = False

    def image(self, frame: int) -> int:
        return self.frames[frame % len(self.frames)]


def _rows(data: bytes, header: int, stride: int):
    if len(data) < header + 4:
        return ()
    offset = struct.unpack_from(">I", data, header)[0]
    if offset == 0:
        return ()
    if offset + 4 > len(data):
        raise ValueError("Texture animation section outside model")
    count = struct.unpack_from(">I", data, offset)[0]
    if count > 256 or offset + 4 + count * stride > len(data):
        raise ValueError("Truncated texture animation descriptors")
    return tuple(data[offset + 4 + i * stride:offset + 4 + (i + 1) * stride] for i in range(count))


def map_animations(data: bytes):
    result = []
    for row in _rows(data, 0x48, 0x7C):
        segment, group, ticks, count = row[:4]
        if not 1 <= count <= 28 or not 1 <= segment <= 15 or not ticks:
            raise ValueError("Invalid map texture animation")
        frames = struct.unpack_from(f">{count}I", row, 12)
        result.append(TextureAnimation(segment, frames, ticks, 7, group))
    return tuple(result)


def prop_animations(data: bytes):
    result = []
    for row in _rows(data, 0x6C, 0x84):
        first, blend, ticks, count = struct.unpack_from(">4I", row)
        if not 1 <= count <= 29 or not ticks:
            raise ValueError("Invalid prop texture animation")
        frames = (first,) + struct.unpack_from(f">{count - 1}I", row, 16)
        result.append(TextureAnimation(first, frames, ticks, 7, interpolate=bool(blend)))
    return tuple(result)
