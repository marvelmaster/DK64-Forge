"""Generic F3DEX2 mesh decoder for DK64 props (table 4), maps (table 1) and actors (table 5).

The Kong decoder (rom_model.decode_actor_mesh) stays the verified reference for the
playable characters. This decoder renders any model statically:

- Vertices are 16-byte N64 `Vtx`: s16 x/y/z, pad, s16 s/t (S10.5), then either
  a signed normal (G_LIGHTING set) or RGBA (vertex colour, "shade").
- Segment bases: props `G_VTX` segment 8 = header 0x48 (DK64 Randomizer model_port.py);
  maps segment 6 = map vertex start (0x38) + chunk vertex offset, display lists by
  chunk (header 0x68, 52-byte records; dk64_lib facts); actors segment 3 = vertex
  block with bone rest offsets from `G_MTX` segment 4 (Phase 2A).
- UVs follow the verified Phase 1F rules (G_TEXTURE scale, tile shift, tile
  upper-left, bilerp texel centre). Generated (G_TEXTURE_GEN) faces use the spherical
  texgen of a fixed front camera (Phase 1G); props/maps rarely use it.
- Textures are decoded with dk64_forge.core.texture_bank from the load state at
  draw time; images on segments other than 0 (dynamic) are drawn untextured unless an
  actor's dynamic slot provides a first frame.
- Prop segment-9 matrices can be supplied by the embedded-track evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import struct
import numpy as np

from . import rom_model, texture_bank, rdp

G_CULL_BACK, G_LIGHTING, G_TEXTURE_GEN = 0x00000400, 0x00020000, 0x00040000
SLOTS = 64  # F3DEX2 vertex buffer


@dataclass(frozen=True)
class DrawTexture:
    """Everything needed to decode and sample one texture as drawn."""
    image: int            # index into `table`
    usage: texture_bank.TextureUsage
    wrap_s: int           # G_TX_WRAP 0 / MIRROR 1 / CLAMP 2
    wrap_t: int
    table: int = 25       # 25 static geometry, 7 source-proven animation frames


@dataclass
class StaticMesh:
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    colors: list[tuple[float, float, float, float]] = field(default_factory=list)
    textures: list[DrawTexture | None] = field(default_factory=list)  # one per triangle
    secondary_textures: list[DrawTexture | None] = field(default_factory=list)
    secondary_uvs: list[tuple[float, float]] = field(default_factory=list)
    joints: list[int] = field(default_factory=list)
    materials: list[rdp.MaterialState] = field(default_factory=list)
    culled: list[bool] = field(default_factory=list)                 # G_CULL_BACK per triangle
    stats: dict = field(default_factory=dict)

    @property
    def triangle_count(self) -> int:
        return len(self.textures)


@dataclass
class _Vertex:
    position: tuple[float, float, float]
    s: int
    t: int
    rgba: bytes
    lit: bool
    texgen: bool
    joint: int = 0


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class _State:
    def __init__(self) -> None:
        self.material = rdp.MaterialState()
        self.geometry = G_LIGHTING
        self.scale = (0xFFFF, 0xFFFF)
        self.texture_on = False
        self.image = None       # (address, fmt, size)
        self.tiles: dict[int, dict] = {}
        self.sizes: dict[int, tuple[int, int, int, int]] = {}
        self.loaded_images = {}
        self.loaded = None      # (address, interleaved)
        self.palette = None
        self.bilerp = True
        self.cache: list[_Vertex | None] = [None] * SLOTS


def decode(data: bytes, ranges, *, rom: bytes, bone_offsets=None, dynamic=None,
           conditional_mask: int | None = None, dynamic_table: int = 25, image_overrides=None, dynamic_groups=None, matrices=None, max_triangles: int = 400_000) -> StaticMesh:
    """Decode display-list byte ranges into a static, textured triangle list.

    ranges: iterable of (start, end, {segment: vertex_base_offset_in_data}).
    bone_offsets: actor bone rest translations (index -> xyz), selected by G_MTX seg 4.
    dynamic: actor dynamic texture slots {segment: (table-25 frames, ...)}.
    conditional_mask: actors only. A non-pushing G_DL to segment 7/8 offset 0 opens a block
        up to a `00000000 0000000s` marker that the game draws when bit (segment - 7) of
        actor->unk146 is set (func_global_asm_80614C38, as in rom_model.decode_actor_mesh).
        Kongs use their hand mask; other actors the generic spawn default -1 (all blocks).
    """
    mesh = StaticMesh()
    tables = _TextureTables(rom)
    unsupported: dict[str, int] = {}
    state = None
    for item in ranges:
        start, end, segments = item[:3]
        range_dynamic = dynamic
        if dynamic_groups is not None and len(item) >= 4:
            range_dynamic = dict(dynamic_groups.get(255, {}))
            range_dynamic.update(dynamic_groups.get(item[3], {}))
        if state is not None and len(item) >= 5 and item[4]:
            # Next piece of the same display list: RDP/RSP state carries over on the
            # console; only the vertices are reloaded from the piece's own block.
            state.cache = [None] * SLOTS
        else:
            state = _State()
        offset = (0.0, 0.0, 0.0)
        current_bone = 0
        matrix = None
        normal_matrix = None
        stack = [(start, end)]
        visited = 0
        while stack:
            pc, stop = stack.pop()
            while pc + 8 <= stop and pc + 8 <= len(data):
                visited += 1
                if visited > 200_000 or mesh.triangle_count >= max_triangles:
                    break
                op = data[pc]
                w0 = int.from_bytes(data[pc:pc + 4], "big")
                w1 = int.from_bytes(data[pc + 4:pc + 8], "big")
                pc += 8
                state.material = state.material.command(w0, w1)
                if op == 0xDF:  # G_ENDDL
                    break
                if op == 0x01:  # G_VTX
                    count = (w0 >> 12) & 0xFF
                    first = ((w0 >> 1) & 0x7F) - count
                    base = segments.get(w1 >> 24)
                    if base is None or count == 0:
                        unsupported["G_VTX segment"] = unsupported.get("G_VTX segment", 0) + 1
                        continue
                    at = base + (w1 & 0xFFFFFF)
                    lit = bool(state.geometry & G_LIGHTING)
                    texgen = bool(state.geometry & G_TEXTURE_GEN)
                    for i in range(count):
                        record = data[at + 16 * i:at + 16 * i + 16]
                        if len(record) < 16 or not 0 <= first + i < SLOTS:
                            break
                        x, y, z, _flag, s, t = struct.unpack(">hhhHhh", record[:12])
                        position = (x + offset[0], y + offset[1], z + offset[2])
                        rgba = record[12:16]
                        if matrix is not None:
                            position = tuple(float(v) for v in (matrix @ (x, y, z, 1))[:3])
                            if lit:
                                normal = np.asarray([_signed(v, 8) for v in rgba[:3]], dtype=float)
                                normal = normal_matrix @ normal
                                length = np.linalg.norm(normal)
                                if length:
                                    normal *= 127 / length
                                rgba = bytes(int(round(v)) & 255 for v in normal) + rgba[3:4]
                        state.cache[first + i] = _Vertex(position, s, t, rgba, lit, texgen, current_bone)
                elif op in (0x05, 0x06, 0x07):  # G_TRI1 / G_TRI2 / G_QUAD
                    triples = [data[pc - 7:pc - 4]]
                    if op != 0x05:
                        triples.append(data[pc - 3:pc])
                    for raw in triples:
                        _emit(mesh, state, [index // 2 for index in raw], tables, range_dynamic, dynamic_table, image_overrides)
                elif op == 0xD9:  # G_GEOMETRYMODE
                    state.geometry = (state.geometry & (w0 & 0xFFFFFF | 0xFF000000)) | w1
                elif op == 0xD7:  # G_TEXTURE
                    state.texture_on = bool(w0 & 2)
                    state.scale = (w1 >> 16, w1 & 0xFFFF)
                elif op == 0xFD:  # G_SETTIMG
                    state.image = (w1, (w0 >> 21) & 7, (w0 >> 19) & 3)
                elif op == 0xF5:  # G_SETTILE
                    state.tiles[(w1 >> 24) & 7] = {
                        "fmt": (w0 >> 21) & 7, "size": (w0 >> 19) & 3, "tmem": w0 & 511,
                        "cmt": (w1 >> 18) & 3, "mask_t": (w1 >> 14) & 15, "shift_t": (w1 >> 10) & 15,
                        "cms": (w1 >> 8) & 3, "mask_s": (w1 >> 4) & 15, "shift_s": w1 & 15}
                elif op == 0xF2:  # G_SETTILESIZE
                    tile = (w1 >> 24) & 7
                    uls, ult = (w0 >> 12) & 0xFFF, w0 & 0xFFF
                    lrs, lrt = (w1 >> 12) & 0xFFF, w1 & 0xFFF
                    state.sizes[tile] = ((lrs - uls) // 4 + 1, (lrt - ult) // 4 + 1, uls, ult)
                elif op == 0xF3 and state.image is not None:  # G_LOADBLOCK
                    state.loaded = (state.image[0], (w1 & 0xFFF) == 0)
                    state.loaded_images[state.tiles.get((w1 >> 24) & 7, {}).get("tmem", 0)] = state.loaded
                elif op == 0xF4 and state.image is not None:  # G_LOADTILE
                    state.loaded = (state.image[0], False)
                    state.loaded_images[state.tiles.get((w1 >> 24) & 7, {}).get("tmem", 0)] = state.loaded
                elif op == 0xF0 and state.image is not None and state.image[0] >> 24 == 0:
                    state.palette = state.image[0] & 0xFFFFFF
                elif op == 0xE3:  # G_SETOTHERMODE_H: texture filter field
                    length = (w0 & 0xFF) + 1
                    shift = 32 - ((w0 >> 8) & 0xFF) - length
                    if shift <= 12 and shift + length >= 14:
                        state.bilerp = ((w1 >> 12) & 3) in (2, 3)
                elif op == 0xDA:  # G_MTX: actor bones select their rest offset
                    if matrices is not None and w1 >> 24 == 9 and (w1 & 0xFFFFFF) in matrices:
                        matrix = matrices[w1 & 0xFFFFFF]
                        normal_matrix = np.linalg.pinv(matrix[:3, :3]).T
                        current_bone = (w1 & 0xFFFFFF) // 64
                    elif bone_offsets is not None and w1 >> 24 == 4:
                        bone = (w1 & 0xFFFFFF) // 0x40
                        current_bone = bone
                        offset = bone_offsets.get(bone, (0.0, 0.0, 0.0))
                    else:
                        unsupported["G_MTX (rest pose)"] = unsupported.get("G_MTX (rest pose)", 0) + 1
                elif (op == 0xDE and conditional_mask is not None and w1 >> 24 in (7, 8)
                      and w1 & 0xFFFFFF == 0 and (w0 >> 16) & 0xFF):
                    segment = w1 >> 24
                    # Included blocks run on; the marker itself is a harmless no-op word.
                    if not (conditional_mask >> (segment - 7)) & 1:
                        marker = next((p for p in range(pc, min(stop, len(data)) - 7, 8)
                                       if data[p] == 0 and int.from_bytes(data[p + 4:p + 8], "big") == segment), None)
                        if marker is not None:
                            pc = marker
                elif op == 0xDE:  # G_DL into the same file (segment with a known base)
                    base = segments.get(w1 >> 24)
                    if base is not None and w1 >> 24 not in (3, 6, 8) and visited < 200_000:
                        target = base + (w1 & 0xFFFFFF)
                        if (w0 >> 16) & 0xFF == 0:  # push: return here afterwards
                            stack.append((pc, stop))
                        pc, stop = target, len(data)
                    elif w1 >> 24 == 5:
                        pass  # actors: segment 5 = runtime hilite state list (decomp code_2F550)
                    else:
                        unsupported["G_DL external"] = unsupported.get("G_DL external", 0) + 1
    mesh.stats = {"triangles": mesh.triangle_count, "unsupported": unsupported}
    return mesh


class _TextureTables:
    """Texture entry sizes; table selection comes from each ROM animation descriptor."""

    def __init__(self, rom: bytes) -> None:
        self.rom = rom
        self._sizes: dict = {}

    def size(self, table: int, index: int) -> int:
        key = (table, index)
        if key not in self._sizes:
            data = texture_bank.table_entry(self.rom, table, index) if self.rom else None
            self._sizes[key] = len(data) if data else 0
        return self._sizes[key]



def _emit(mesh: StaticMesh, state: _State, slots, tables: _TextureTables, dynamic, dynamic_table, image_overrides) -> None:
    if any(not 0 <= slot < SLOTS or state.cache[slot] is None for slot in slots):
        return
    vertices = [state.cache[slot] for slot in slots]
    texture = _draw_texture(state, dynamic, tables, dynamic_table, image_overrides)
    texture1 = _draw_texture(state, dynamic, tables, dynamic_table, image_overrides, tile_index=1)
    mesh.secondary_textures.append(texture1)
    for vertex in vertices:
        mesh.positions.append(vertex.position)
        mesh.joints.append(vertex.joint)
        if vertex.lit:
            # Lit vertices carry a normal: shade with a simple fixed key light (preview only).
            normal = [_signed(b, 8) / 127.0 for b in vertex.rgba[:3]]
            length = math.sqrt(sum(c * c for c in normal)) or 1.0
            light = max(0.0, (0.35 * normal[0] + 0.8 * normal[1] + 0.5 * normal[2]) / length)
            level = 0.55 + 0.45 * light
            mesh.colors.append((level, level, level, vertex.rgba[3] / 255.0))
        else:
            mesh.colors.append(tuple(b / 255.0 for b in vertex.rgba))
        mesh.uvs.append(_uv(state, vertex, texture))
        mesh.secondary_uvs.append(_uv(state, vertex, texture1, tile_index=1))
    mesh.materials.append(state.material)
    mesh.textures.append(texture)
    mesh.culled.append(bool(state.geometry & G_CULL_BACK))


def _draw_texture(state: _State, dynamic, tables: _TextureTables, dynamic_table=25, image_overrides=None, tile_index=0) -> DrawTexture | None:
    if not state.texture_on or state.loaded is None:
        return None
    tile = state.tiles.get(tile_index)
    if tile is None:
        return None
    loaded = state.loaded_images.get(tile.get("tmem", 0), state.loaded if tile_index == 0 else None)
    if loaded is None:
        return None
    address, interleaved = loaded
    segment = address >> 24
    table = 25
    if segment == 0:
        image = address & 0xFFFFFF
        if image_overrides and image in image_overrides:
            table, image = image_overrides[image]
    elif dynamic and segment in dynamic and dynamic[segment]:
        table = dynamic_table
        image = dynamic[segment][0]
    else:
        return None
    if tile_index in state.sizes:
        width, height, _uls, _ult = state.sizes[tile_index]
    elif tile["mask_s"] and tile["mask_t"]:
        width, height = 1 << tile["mask_s"], 1 << tile["mask_t"]
    else:
        return None
    usage = texture_bank.TextureUsage(tile["fmt"], tile["size"], width, height, interleaved,
                                      state.palette if tile["fmt"] == 2 else None, "draw")
    needed = width * height * texture_bank.SIZES.get(tile["size"], 16) // 8
    return DrawTexture(image, usage, tile["cms"], tile["cmt"], table)


def _uv(state: _State, vertex: _Vertex, texture: DrawTexture | None, tile_index=0) -> tuple[float, float]:
    if texture is None:
        return 0.0, 0.0
    width, height = texture.usage.width, texture.usage.height
    tile = state.tiles.get(tile_index, {})
    uls, ult = state.sizes.get(tile_index, (0, 0, 0, 0))[2:]
    if vertex.texgen:
        normal = [_signed(b, 8) / 127.0 for b in vertex.rgba[:3]]
        # Spherical texgen for a front camera (right = +X, up = +Y), Phase 1G formula.
        s = (max(-1.0, min(1.0, normal[0])) + 1.0) * 512 * state.scale[0] / 65536.0
        t = (max(-1.0, min(1.0, normal[1])) + 1.0) * 512 * state.scale[1] / 65536.0
    else:
        s = vertex.s / 32.0 * state.scale[0] / 65536.0
        t = vertex.t / 32.0 * state.scale[1] / 65536.0

    def shift(value: float, amount: int) -> float:
        return value / (2 ** amount) if 0 < amount <= 10 else (
            value * (2 ** (16 - amount)) if amount > 10 else value)
    s, t = shift(s, tile.get("shift_s", 0)), shift(t, tile.get("shift_t", 0))
    centre = 0.5 if state.bilerp else 0.0
    return (s - uls / 4.0 + centre) / width, (t - ult / 4.0 + centre) / height


# --- model sources ------------------------------------------------------------------------

def prop_ranges(data: bytes):
    """Model-two prop: header 0x40..0x48 holds several display lists back to back (a setup
    list, then the geometry, each closed by G_ENDDL); segment 8 = vertex block at 0x48."""
    start = int.from_bytes(data[0x40:0x44], "big")
    end = int.from_bytes(data[0x48:0x4C], "big")
    if not 0 <= start < end <= len(data):
        return []
    ranges, begin = [], start
    for at in range(start, end - 7, 8):
        if data[at] == 0xDF:
            ranges.append((begin, at + 8, {8: end}))
            begin = at + 8
    if begin < end:
        ranges.append((begin, end, {8: end}))
    return ranges


def _list_pieces(data: bytes, begin: int, size: int):
    """A chunk display list holds one or more G_ENDDL-terminated pieces back to back."""
    pieces, at = [], begin
    for pc in range(begin, min(begin + size, len(data)) - 7, 8):
        if data[pc] == 0xDF:
            pieces.append((at, pc + 8))
            at = pc + 8
    if at < begin + size:
        pieces.append((at, begin + size))
    return pieces


def _vertex_extent(data: bytes, begin: int, end: int, dl_start: int, seen=None) -> int:
    """Bytes of segment-6 vertex data a piece loads, following its segment-7 sub-lists."""
    seen = set() if seen is None else seen
    extent, pc = 0, begin
    while pc + 8 <= min(end, len(data)):
        op = data[pc]
        if op == 0x01 and data[pc + 4] == 6:
            count = (int.from_bytes(data[pc:pc + 4], "big") >> 12) & 0xFF
            extent = max(extent, int.from_bytes(data[pc + 5:pc + 8], "big") + 16 * count)
        elif op == 0xDE and data[pc + 4] == 7:
            target = dl_start + int.from_bytes(data[pc + 5:pc + 8], "big")
            if target not in seen:
                seen.add(target)
                extent = max(extent, _vertex_extent(data, target, len(data), dl_start, seen))
            if data[pc + 1]:  # branch: no return
                break
        elif op == 0xDF:
            break
        pc += 8
    return extent


def map_ranges(data: bytes, *, with_chunk: bool = False, stats: dict | None = None):
    """Map geometry: per chunk up to four display lists, each one or more pieces.

    A chunk display list holds one or more G_ENDDL-terminated pieces back to back, all
    drawn (the game binds them through per-piece sub-records, ``func_global_asm_80656B98``).
    Segment 6 is the chunk's vertex block and segment 7 the DL start. Chunks use one of
    two vertex addressing conventions, distinguished structurally:

    * **relative**: every piece addresses its vertices from 0 and the pieces' blocks follow
      each other in display-list order; the summed piece extents equal the chunk's
      vertex size while no single piece reaches its end (e.g. Funky's store, map 1);
    * **absolute**: pieces address the whole chunk block (extents grow to the block size,
      e.g. Japes); all pieces share the chunk base.

    Pieces that another piece calls through segment 7 are shared sub-lists: they are
    drawn through their caller only, not a second time on their own.

    ``stats`` (optional) counts the chunks per convention and the skipped sub-lists.
    """
    dl_start = int.from_bytes(data[0x34:0x38], "big")
    vertex_start = int.from_bytes(data[0x38:0x3C], "big")
    chunk_start = int.from_bytes(data[0x68:0x6C], "big")
    chunk_end = int.from_bytes(data[0x6C:0x70], "big")
    ranges, chunks = [], []
    for chunk, at in enumerate(range(chunk_start, chunk_end - 51, 52)):
        words = [int.from_bytes(data[at + k:at + k + 4], "big") for k in range(12, 52, 4)]
        vertex_offset, vertex_size = words[8], words[9]
        pieces = []
        for dl_offset, size in zip(words[0:8:2], words[1:8:2]):
            if dl_offset == 0xFFFFFFFF or size == 0:
                continue
            for index, (begin, end) in enumerate(_list_pieces(data, dl_start + dl_offset, size)):
                pieces.append((begin, end, index > 0, _vertex_extent(data, begin, end, dl_start)))
        if not pieces:
            continue
        extents = [piece[3] for piece in pieces]
        relative = (len(pieces) > 1 and sum(extents) == vertex_size and max(extents) < vertex_size)
        if stats is not None:
            key = "relative_chunks" if relative else "absolute_chunks"
            stats[key] = stats.get(key, 0) + 1
        used = 0
        for begin, end, continued, extent in pieces:
            base = used if relative else 0
            used += extent
            row = (begin, end, {6: vertex_start + vertex_offset + base, 7: dl_start})
            chunks.append(row + (chunk, continued) if with_chunk else row)
    called = {dl_start + int.from_bytes(data[pc + 5:pc + 8], "big")
              for row in chunks for pc in range(row[0], row[1] - 7, 8)
              if data[pc] == 0xDE and data[pc + 4] == 7}
    for row in chunks:
        if row[0] in called:
            if stats is not None:
                stats["shared_sublists"] = stats.get("shared_sublists", 0) + 1
            continue
        ranges.append(row)
    return ranges


def actor_ranges(actor: rom_model.Actor):
    return [(root, actor.dl_end, {3: actor.vertices_start}) for root in actor.root_offsets]


def actor_bone_offsets(actor: rom_model.Actor) -> dict[int, tuple[float, float, float]]:
    return {index: tuple(bone.accumulated) for index, bone in enumerate(actor.bones)}
