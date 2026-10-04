"""Setup objects (table 9) and character spawns (table 16), never executed.

Layout facts: DK64 Randomizer build/encoders.py; prop type is the table-4
index (decomp code_36880.c, 806368F0). Setup actors and character spawners
are drawn with the model the game's own definition tables assign
(core.actor_tables: model, 0.15-based scale, y rotation); entries without a
model (controllers, spawners of effects) stay labelled markers. Spawn
conditions, actor scripts and animation are not executed.
"""
from dataclasses import dataclass, replace
import math
import struct
import numpy as np
from .core import texture_bank
from .core.actor_tables import ANGLE_UNITS, load_actor_tables, setup_actor_scale, spawner_scale
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
    raw_scale: float | None = None  # file value before the game's 0.15-based actor scaling


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
        y_rotation = struct.unpack_from(">h", row, 0x30)[0]  # func_global_asm_80688FC0 argument
        result.append(Placement("actor", index, int.from_bytes(row[0x32:0x34], "big") + 0x10,
                                (x, y, z), setup_actor_scale(scale),
                                (0., y_rotation * 360. / ANGLE_UNITS, 0.),
                                int.from_bytes(row[0x34:0x36], "big"), scale))
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
        y_rotation = int.from_bytes(row[2:4], "big")  # Randomizer field "y_rot" (12-bit angle)
        result.append(Placement("character spawn", index, row[0], position, spawner_scale(row[15]),
                                (0., y_rotation * 360. / ANGLE_UNITS, 0.), row[19], row[15]))
        at += 0x16 + row[17] * 2
        if at > len(data):
            raise ValueError("Truncated character-spawn extra data")
    return tuple(result)


def placements(rom: bytes, map_id: int):
    setup = texture_bank.table_entry(rom, 9, map_id)
    spawners = texture_bank.table_entry(rom, 16, map_id)
    return ((parse_setup(setup) if setup else ()) +
            (parse_spawners(spawners) if spawners else ()))


def texture_signature(texture):
    return (texture.width, texture.height, texture.rgba, texture.wrap_s, texture.wrap_t, texture.mip_levels)


def merge_render(scenes):
    shared = {}
    positions, uvs, colors, batches, textures, uvs1 = [], [], [], [], [], []
    for scene in scenes:
        first = len(positions)
        ids = {}
        for texture in scene.textures:
            key = texture_signature(texture)
            if key not in shared:
                shared[key] = len(textures)
                textures.append(replace(texture, texture_index=len(textures)))
            ids[texture.texture_index] = shared[key]
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
    # Opaque/masked placements sharing material can use one draw call. Keep
    # blended batches separate for depth sorting and billboards for their pivots.
    groups = {}
    for index, batch in enumerate(batches):
        key = index if batch.alpha_mode == "BLEND" or batch.billboard_center is not None else replace(batch, first_vertex=0, vertex_count=0)
        groups.setdefault(key, []).append(batch)
    compact_p, compact_uv, compact_uv1, compact_colors, compact_batches = [], [], [], [], []
    for group in groups.values():
        first = len(compact_p)
        for batch in group:
            span = slice(batch.first_vertex, batch.first_vertex + batch.vertex_count)
            compact_p.extend(positions[span]); compact_uv.extend(uvs[span])
            compact_uv1.extend(uvs1[span]); compact_colors.extend(colors[span])
        compact_batches.append(replace(group[0], first_vertex=first, vertex_count=len(compact_p)-first))
    positions, uvs, uvs1, colors, batches = compact_p, compact_uv, compact_uv1, compact_colors, compact_batches
    lo = tuple(min(p[k] for p in positions) for k in range(3))
    hi = tuple(max(p[k] for p in positions) for k in range(3))
    return PreparedRenderData(tuple(positions), tuple(uvs), tuple(batches), tuple(textures), lo, hi, tuple(colors), tuple(uvs1))


def placed_render(render, placement):
    x, y, z = np.radians(placement.angles)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    rx = np.array(((1, 0, 0), (0, cx, -sx), (0, sx, cx)))
    ry = np.array(((cy, 0, sy), (0, 1, 0), (-sy, 0, cy)))
    rz = np.array(((cz, -sz, 0), (sz, cz, 0), (0, 0, 1)))
    rotation = np.eye(3) if any(b.billboard_center is not None for b in render.batches) else rz @ ry @ rx
    points = np.asarray(render.positions) @ rotation.T * placement.scale + placement.position
    return replace(render, positions=tuple(tuple(float(v) for v in p) for p in points),
                   batches=tuple(replace(b, billboard_center=placement.position) if b.billboard_center is not None else b for b in render.batches),
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


def actor_entry(tables, placement):
    """Table-5 entry the game spawns for an actor or character-spawn placement, if any."""
    if placement.kind == "actor":
        definition = tables.setup_def(placement.type_id)
    elif placement.kind == "character spawn":
        definition = tables.enemy_def(placement.type_id)
    else:
        return None
    return definition.table5_entry if definition else None


def content_render(rom, map_id, cache, *, tick=0, night=False, hidden=(), models=None):
    rows = placements(rom, map_id)
    if night and map_id == 48:
        from .core.control_states import global_asm_data, GLOBAL_ASM_DATA_VRAM
        data = global_asm_data(rom)
        at = 0x80755698 - GLOBAL_ASM_DATA_VRAM
        swaps = {28: 0x63, 9: 0x54, 44: 0x67}
        rows = tuple(replace(row, type_id=swaps[row.type_id])
                     if row.kind == "character spawn" and row.type_id in swaps and data[at + row.type_id] != 10
                     else row for row in rows)
    rows = tuple(row for row in rows if (row.kind, row.index) not in hidden)
    tables = load_actor_tables(rom)
    props, actors, scenes, missing = {}, {}, [], []
    models = {} if models is None else models
    for row in rows:
        if row.kind == "prop":
            if row.type_id not in props:
                data = texture_bank.table_entry(rom, 4, row.type_id)
                from .core.texture_animation import prop_animations
                dynamic = bool(data and prop_animations(data))
                key = ("prop", row.type_id, tick if dynamic else 0)
                if key not in models:
                    models[key] = static_model.prop_model(rom, row.type_id, cache, tick=tick)
                props[row.type_id] = models[key]
            model = props[row.type_id]
        else:
            entry = actor_entry(tables, row)
            if entry is not None and entry not in actors:
                key = ("actor", entry, 0)
                if key not in models:
                    models[key] = static_model.actor_model(rom, entry, cache)
                actors[entry] = models[key]
            model = actors.get(entry)
        if model and model.triangles:
            scenes.append(placed_render(model.render, row))
        else:
            missing.append(row)
            scenes.append(spawn_marker(row))
    # Animated frames are transient; retain only the current tick and static models.
    for key in tuple(models):
        if key[0] == "prop" and key[2] not in (0, tick):
            del models[key]
    return merge_render(scenes), rows, tuple(missing)
