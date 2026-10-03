"""Setup objects (table 9) and character spawns (table 16), never executed.

Layout facts: DK64 Randomizer build/encoders.py; prop type is the table-4
index (decomp code_36880.c, 806368F0). Actors requiring game logic are
shown as labelled spawn markers rather than assigned an arbitrary mesh.
"""
from dataclasses import dataclass, replace
import math
import struct
import numpy as np
from .core import texture_bank
from .render_data import PreparedRenderData, PreparedBatch
from . import static_model


@dataclass(frozen=True)
class Placement:
    kind: str
    index: int
    type_id: int
    position: tuple[float, float, float]
    scale: float = 1.
    angles: tuple[float, float, float] = (0., 0., 0.)
    object_id: int | None = None


def _count(data, offset, size, stride):
    if offset + size > len(data):
        raise ValueError("Truncated level section count")
    count = int.from_bytes(data[offset:offset + size], "big")
    if count > 10000 or offset + size + count * stride > len(data):
        raise ValueError("Level section exceeds file bounds")
    return count, offset + size


def parse_setup(data: bytes):
    count, at = _count(data, 0, 4, 0x30)
    result = []
    for index in range(count):
        row = data[at + index * 0x30:at + (index + 1) * 0x30]
        x, y, z, scale = struct.unpack_from(">4f", row)
        angles = struct.unpack_from(">3f", row, 0x18)
        type_id, object_id = struct.unpack_from(">2H", row, 0x28)
        if not all(math.isfinite(v) for v in (x, y, z, scale, *angles)):
            raise ValueError("Non-finite setup transform")
        result.append(Placement("prop", index, type_id, (x, y, z), scale, angles, object_id))
    at += count * 0x30
    conveyors, at = _count(data, at, 4, 0x24)
    at += conveyors * 0x24
    count, at = _count(data, at, 4, 0x38)
    for index in range(count):
        row = data[at + index * 0x38:at + (index + 1) * 0x38]
        x, y, z, scale = struct.unpack_from(">4f", row)
        if not all(math.isfinite(v) for v in (x, y, z, scale)):
            raise ValueError("Non-finite actor-spawn transform")
        result.append(Placement("actor", index, int.from_bytes(row[0x32:0x34], "big") + 0x10,
                                (x, y, z), scale))
    return tuple(result)


def parse_spawners(data: bytes):
    fences, at = _count(data, 0, 2, 0)
    for _ in range(fences):
        points, at = _count(data, at, 2, 6)
        at += points * 6
        points, at = _count(data, at, 2, 10)
        at += points * 10 + 4
    count, at = _count(data, at, 2, 0x16)
    result = []
    for index in range(count):
        if at + 0x16 > len(data):
            raise ValueError("Truncated character spawn")
        row = data[at:at + 0x16]
        position = struct.unpack_from(">3h", row, 4)
        result.append(Placement("character spawn", index, row[0], position,
                                row[15] / 100., object_id=row[19]))
        at += 0x16 + row[17] * 2
        if at > len(data):
            raise ValueError("Truncated character-spawn extra data")
    return tuple(result)


def placements(rom: bytes, map_id: int):
    setup = texture_bank.table_entry(rom, 9, map_id)
    spawners = texture_bank.table_entry(rom, 16, map_id)
    return ((parse_setup(setup) if setup else ()) +
            (parse_spawners(spawners) if spawners else ()))


def merge_render(scenes):
    positions, uvs, colors, batches, textures, uvs1 = [], [], [], [], [], []
    for scene in scenes:
        first = len(positions)
        ids = {texture.texture_index: len(textures) + i for i, texture in enumerate(scene.textures)}
        textures.extend(replace(t, texture_index=ids[t.texture_index]) for t in scene.textures)
        positions.extend(scene.positions)
        uvs.extend(scene.uvs)
        uvs1.extend(scene.uvs1 or scene.uvs)
        colors.extend(scene.colors or ((1., 1., 1., 1.),) * len(scene.positions))
        batches.extend(replace(b, first_vertex=b.first_vertex + first,
                               texture_index=None if b.texture_index is None else ids[b.texture_index],
                               texture1_index=None if b.texture1_index is None else ids[b.texture1_index])
                       for b in scene.batches)
    if not positions:
        return None
    lo = tuple(min(p[k] for p in positions) for k in range(3))
    hi = tuple(max(p[k] for p in positions) for k in range(3))
    return PreparedRenderData(tuple(positions), tuple(uvs), tuple(batches), tuple(textures), lo, hi, tuple(colors), tuple(uvs1))


def placed_render(render, placement):
    x, y, z = np.radians(placement.angles)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx = np.array(((1, 0, 0), (0, cx, -sx), (0, sx, cx)))
    ry = np.array(((cy, 0, sy), (0, 1, 0), (-sy, 0, cy)))
    rz = np.array(((cz, -sz, 0), (sz, cz, 0), (0, 0, 1)))
    points = np.asarray(render.positions) @ (ry @ rx @ rz).T * placement.scale + placement.position
    return replace(render, positions=tuple(tuple(float(v) for v in p) for p in points),
                   bounds_minimum=tuple(points.min(axis=0)), bounds_maximum=tuple(points.max(axis=0)))


def spawn_marker(placement):
    x, y, z = placement.position
    r = 12.
    vertices = ((x+r, y, z), (x-r, y, z), (x, y+r, z), (x, y-r, z), (x, y, z+r), (x, y, z-r))
    faces = ((0,2,4),(4,2,1),(1,2,5),(5,2,0),(4,3,0),(1,3,4),(5,3,1),(0,3,5))
    points = tuple(vertices[i] for face in faces for i in face)
    color = (0.2, 0.8, 1., 1.) if placement.kind == "actor" else (1., 0.65, 0.15, 1.)
    return PreparedRenderData(points, ((0.,0.),)*24,
                              (PreparedBatch(0, 24, None, True, False, color, "spawn marker"),), (),
                              (x-r,y-r,z-r), (x+r,y+r,z+r))


def content_render(rom, map_id, cache, *, tick=0):
    rows = placements(rom, map_id)
    models, scenes, missing = {}, [], []
    for row in rows:
        if row.kind != "prop":
            scenes.append(spawn_marker(row))
            continue
        if row.type_id not in models:
            models[row.type_id] = static_model.prop_model(rom, row.type_id, cache, tick=tick)
        model = models[row.type_id]
        if model and model.triangles:
            scenes.append(placed_render(model.render, row))
        else:
            missing.append(row)
            scenes.append(spawn_marker(row))
    return merge_render(scenes), rows, tuple(missing)
