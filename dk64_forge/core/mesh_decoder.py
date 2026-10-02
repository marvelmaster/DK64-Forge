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
- `G_MTX` in props (animated props) is ignored: parts are drawn at their rest
  positions in the model's own space (JFG Forge "rest pose").
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import struct

from . import rom_model, texture_bank

G_CULL_BACK, G_LIGHTING, G_TEXTURE_GEN = 0x00000400, 0x00020000, 0x00040000
SLOTS = 64  # F3DEX2 vertex buffer


@dataclass(frozen=True)
class DrawTexture:
    """Everything needed to decode and sample one texture as drawn."""
    image: int            # index into `table`
    usage: texture_bank.TextureUsage
    wrap_s: int           # G_TX_WRAP 0 / MIRROR 1 / CLAMP 2
    wrap_t: int
    table: int = 25       # 25 geometry textures, or 7 (uncompressed) as a fallback


@dataclass
class StaticMesh:
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    colors: list[tuple[float, float, float, float]] = field(default_factory=list)
    textures: list[DrawTexture | None] = field(default_factory=list)  # one per triangle
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


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class _State:
    def __init__(self) -> None:
        self.geometry = G_LIGHTING
        self.scale = (0xFFFF, 0xFFFF)
        self.texture_on = False
        self.image = None       # (address, fmt, size)
        self.tiles: dict[int, dict] = {}
        self.sizes: dict[int, tuple[int, int, int, int]] = {}
        self.loaded = None      # (address, interleaved)
        self.palette = None
        self.bilerp = True
        self.cache: list[_Vertex | None] = [None] * SLOTS


def decode(data: bytes, ranges, *, rom: bytes, bone_offsets=None, dynamic=None,
           conditional_mask: int | None = None, max_triangles: int = 400_000) -> StaticMesh:
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
    for start, end, segments in ranges:
        state = _State()
        offset = (0.0, 0.0, 0.0)
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
                        state.cache[first + i] = _Vertex(
                            (x + offset[0], y + offset[1], z + offset[2]), s, t, record[12:16], lit, texgen)
                elif op in (0x05, 0x06, 0x07):  # G_TRI1 / G_TRI2 / G_QUAD
                    triples = [data[pc - 7:pc - 4]]
                    if op != 0x05:
                        triples.append(data[pc - 3:pc])
                    for raw in triples:
                        _emit(mesh, state, [index // 2 for index in raw], tables, dynamic)
                elif op == 0xD9:  # G_GEOMETRYMODE
                    state.geometry = (state.geometry & (w0 & 0xFFFFFF | 0xFF000000)) | w1
                elif op == 0xD7:  # G_TEXTURE
                    state.texture_on = bool(w0 & 2)
                    state.scale = (w1 >> 16, w1 & 0xFFFF)
                elif op == 0xFD:  # G_SETTIMG
                    state.image = (w1, (w0 >> 21) & 7, (w0 >> 19) & 3)
                elif op == 0xF5:  # G_SETTILE
                    state.tiles[(w1 >> 24) & 7] = {
                        "fmt": (w0 >> 21) & 7, "size": (w0 >> 19) & 3,
                        "cmt": (w1 >> 18) & 3, "mask_t": (w1 >> 14) & 15, "shift_t": (w1 >> 10) & 15,
                        "cms": (w1 >> 8) & 3, "mask_s": (w1 >> 4) & 15, "shift_s": w1 & 15}
                elif op == 0xF2:  # G_SETTILESIZE
                    tile = (w1 >> 24) & 7
                    uls, ult = (w0 >> 12) & 0xFFF, w0 & 0xFFF
                    lrs, lrt = (w1 >> 12) & 0xFFF, w1 & 0xFFF
                    state.sizes[tile] = ((lrs - uls) // 4 + 1, (lrt - ult) // 4 + 1, uls, ult)
                elif op == 0xF3 and state.image is not None:  # G_LOADBLOCK
                    state.loaded = (state.image[0], (w1 & 0xFFF) == 0)
                elif op == 0xF4 and state.image is not None:  # G_LOADTILE
                    state.loaded = (state.image[0], False)
                elif op == 0xF0 and state.image is not None and state.image[0] >> 24 == 0:
                    state.palette = state.image[0] & 0xFFFFFF
                elif op == 0xE3:  # G_SETOTHERMODE_H: texture filter field
                    length = (w0 & 0xFF) + 1
                    shift = 32 - ((w0 >> 8) & 0xFF) - length
                    if shift <= 12 and shift + length >= 14:
                        state.bilerp = ((w1 >> 12) & 3) in (2, 3)
                elif op == 0xDA:  # G_MTX: actor bones select their rest offset
                    if bone_offsets is not None and w1 >> 24 == 4:
                        bone = (w1 & 0xFFFFFF) // 0x40
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
    """Chooses the pointer table of a segment-0 image index.

    DIAGNOSTIC ASSUMPTION: display lists name images by index; maps and actors resolve to
    table 25. Some props (e.g. the torch flame, 16x64 RGBA32 frames 0x8C-0x97) only fit an
    entry of table 7 (uncompressed textures) exactly while their table-25 entry is too
    small. So: table 25 when its entry is large enough, else table 7 when it is.
    """

    def __init__(self, rom: bytes) -> None:
        self.rom = rom
        self._sizes: dict = {}

    def size(self, table: int, index: int) -> int:
        key = (table, index)
        if key not in self._sizes:
            data = texture_bank.table_entry(self.rom, table, index) if self.rom else None
            self._sizes[key] = len(data) if data else 0
        return self._sizes[key]

    def choose(self, index: int, needed: int) -> int:
        if self.size(25, index) >= needed or self.size(7, index) < needed:
            return 25
        return 7


def _emit(mesh: StaticMesh, state: _State, slots, tables: _TextureTables, dynamic) -> None:
    if any(not 0 <= slot < SLOTS or state.cache[slot] is None for slot in slots):
        return
    vertices = [state.cache[slot] for slot in slots]
    texture = _draw_texture(state, dynamic, tables)
    for vertex in vertices:
        mesh.positions.append(vertex.position)
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
    mesh.textures.append(texture)
    mesh.culled.append(bool(state.geometry & G_CULL_BACK))


def _draw_texture(state: _State, dynamic, tables: _TextureTables) -> DrawTexture | None:
    if not state.texture_on or state.loaded is None:
        return None
    tile = state.tiles.get(0)
    if tile is None:
        return None
    address, interleaved = state.loaded
    segment = address >> 24
    if segment == 0:
        image = address & 0xFFFFFF
    elif dynamic and segment in dynamic and dynamic[segment]:
        image = dynamic[segment][0]  # first frame, as for the Kongs
    else:
        return None
    if 0 in state.sizes:
        width, height, _uls, _ult = state.sizes[0]
    elif tile["mask_s"] and tile["mask_t"]:
        width, height = 1 << tile["mask_s"], 1 << tile["mask_t"]
    else:
        return None
    usage = texture_bank.TextureUsage(tile["fmt"], tile["size"], width, height, interleaved,
                                      state.palette if tile["fmt"] == 2 else None, "draw")
    needed = width * height * texture_bank.SIZES.get(tile["size"], 16) // 8
    return DrawTexture(image, usage, tile["cms"], tile["cmt"], tables.choose(image, needed))


def _uv(state: _State, vertex: _Vertex, texture: DrawTexture | None) -> tuple[float, float]:
    if texture is None:
        return 0.0, 0.0
    width, height = texture.usage.width, texture.usage.height
    tile = state.tiles.get(0, {})
    uls, ult = state.sizes.get(0, (0, 0, 0, 0))[2:]
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


def map_ranges(data: bytes):
    """Map geometry: per chunk up to four display lists using the chunk's vertex block."""
    dl_start = int.from_bytes(data[0x34:0x38], "big")
    vertex_start = int.from_bytes(data[0x38:0x3C], "big")
    chunk_start = int.from_bytes(data[0x68:0x6C], "big")
    chunk_end = int.from_bytes(data[0x6C:0x70], "big")
    ranges = []
    for at in range(chunk_start, chunk_end - 51, 52):
        words = [int.from_bytes(data[at + k:at + k + 4], "big") for k in range(12, 52, 4)]
        vertex_offset = words[8]
        for dl_offset, size in zip(words[0:8:2], words[1:8:2]):
            if dl_offset == 0xFFFFFFFF or size == 0:
                continue
            begin = dl_start + dl_offset
            ranges.append((begin, begin + size, {6: vertex_start + vertex_offset, 7: dl_start}))
    return ranges


def actor_ranges(actor: rom_model.Actor):
    return [(root, actor.dl_end, {3: actor.vertices_start}) for root in actor.root_offsets]


def actor_bone_offsets(actor: rom_model.Actor) -> dict[int, tuple[float, float, float]]:
    return {index: tuple(bone.accumulated) for index, bone in enumerate(actor.bones)}
