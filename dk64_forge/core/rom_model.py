"""Narrow DK64 US, actor-table-5 entry-3 static geometry experiment.

The input ROM is never modified. No extracted data is bundled with this source.
Evidence: research/PHASE0_ECOSYSTEM.md and research/PHASE1_STATIC_DK.md.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import struct
import sys
import zlib
import binascii
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from . import texgen


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_OUTPUT = REPO_ROOT / "local_output"
EXPECTED_SIZE = 0x2000000
EXPECTED_XXH3 = 0x4D876060F09B3FC5
POINTER_BASE = 0x101C50
TABLE = 5
ENTRY = 3
SLOT_COUNT = 32
G_LIGHTING = 0x00020000
# G_SETCOMBINE words (w0 & 0xFFFFFF, w1) whose two cycles are (0 - 0) * 0 + SHADE:
# the RDP outputs the lit vertex colour only and ignores any bound texture.
G_CC_SHADE_ONLY = (0xFFFFFF, 0xFFFE793C)
G_MDSFT_ZMODE = 10
ZMODE_DECAL = 3  # G_ZS decal: coplanar surfaces pass the depth test with LEQUAL
# G_SETOTHERMODE_H texture filter field (gbi.h G_MDSFT_TEXTFILT, 2 bits).
G_MDSFT_TEXTFILT = 12
G_TF_POINT, G_TF_AVERAGE, G_TF_BILERP = 0, 3, 2
# Fixed camera for baking view-dependent G_TEXTURE_GEN into glTF TEXCOORD_0.
# DIAGNOSTIC ASSUMPTION: a front view of the origin-centered bind pose (DK faces
# +Z); only the direction matters for spherical texgen. In game the camera moves.
TEXGEN_BAKE_CAMERA = {"eye": (0.0, 0.0, 1.0), "at": (0.0, 0.0, 0.0), "up": (0.0, 1.0, 0.0)}
# guLookAtHilite light directions D_807444CC..D4 / D_807444D8..E0 (code_450.c:617).
# VERIFIED static initial values in the global_asm .data overlay (ROM gzip 0xC29D4,
# vram 0x80744460 + 0x6C): both (1, 1, 1). No decompiled C writes them (LIKELY constant).
HILITE_LIGHTS = ((1.0, 1.0, 1.0), (1.0, 1.0, 1.0))
KNOWN_STATE = {
    0x00, 0xD7, 0xD9, 0xDC, 0xE0, 0xE1, 0xE2, 0xE3,
    0xE6, 0xE7, 0xE8, 0xE9, 0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5,
    0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD,
}
GEOMETRY_AFFECTING = {0x02, 0x03, 0x04, 0x07, 0xD8, 0xDB, 0xDD}


class ModelError(ValueError):
    pass


def decode_n64_normal(raw: bytes) -> tuple[float, float, float]:
    """Decode signed N64 normal bytes to a unit glTF normal, retaining zero."""
    need(len(raw) == 3, "N64 normal must contain three signed bytes")
    signed = struct.unpack("bbb", raw)
    converted = tuple(max(-1.0, value / 127.0) for value in signed)
    length = math.sqrt(sum(value * value for value in converted))
    if length == 0.0:
        return (0.0, 0.0, 0.0)
    return tuple(value / length for value in converted)


def parse_dynamic_textures(actor: Actor) -> dict[int, tuple[int, ...]]:
    """Read the actor's DK64 dynamic-texture header (Randomizer's documented layout)."""
    at = actor.dynamic_texture_offset
    need(at + 2 <= len(actor.data), "dynamic-texture header truncated")
    count = int.from_bytes(actor.data[at:at + 2], "big")
    at += 2
    need(count <= 64, "implausible dynamic-texture slot count")
    result = {}
    for _ in range(count):
        need(at + 6 <= len(actor.data), "dynamic-texture record truncated")
        frame_count, slot, layers = struct.unpack_from(">HHH", actor.data, at)
        at += 6
        need(frame_count <= 256 and layers <= 16, "implausible dynamic-texture record")
        frame_total = frame_count * layers
        need(at + frame_total * 2 <= len(actor.data), "dynamic-texture frames truncated")
        values = struct.unpack_from(f">{frame_total}H", actor.data, at) if frame_total else ()
        result[slot] = tuple(values[:frame_count])
        at += frame_total * 2
    return result


def signed_s16(value: int) -> int:
    return value if value < 0 else (value - 0x10000 if value & 0x8000 else value)


def actor_uv(raw_s: int, raw_t: int, use: TextureUse) -> tuple[float, float]:
    """N64 Vtx tc is signed 10.5 texels; G_TEXTURE scales are unsigned 0.16."""
    s = signed_s16(raw_s) / 32.0 * (use.scale_s / 65536.0)
    t = signed_s16(raw_t) / 32.0 * (use.scale_t / 65536.0)
    return tile_texels_to_uv(s, t, use)


def tile_texels_to_uv(s: float, t: float, use: TextureUse) -> tuple[float, float]:
    """Map scaled S/T texels through the render tile to glTF/OpenGL UVs."""
    # G_SETTILE shifts 1..10 right and 11..15 left (16-shift), in texel space.
    def shift(value: float, amount: int) -> float:
        return value / (2 ** amount) if 0 < amount <= 10 else (
            value * (2 ** (16 - amount)) if amount > 10 else value)
    s, t = shift(s, use.shift_s), shift(t, use.shift_t)
    # RDP render-tile upper left is in quarter texels, subtracted after shift.
    s, t = s - use.tile_uls / 4.0, t - use.tile_ult / 4.0
    # RDP bilinear filtering puts texel centers on integer coordinates (RT64
    # TextureSampler.hlsli: floor(uv) base texel, frac weights); glTF/OpenGL
    # centers are at +0.5. Point sampling floors in both conventions.
    center = 0.5 if use.texture_filter in (G_TF_BILERP, G_TF_AVERAGE) else 0.0
    return (s + center) / use.sample_width, (t + center) / use.sample_height


def rgba16_to_png(data: bytes, width: int, height: int,
                  odd_lines_swapped: bool = False) -> bytes:
    """Small local RGBA16 decoder for the format used by canonical DK.

    odd_lines_swapped: the image was loaded by G_LOADBLOCK with dxt=0, so the
    RDP did not interleave odd lines during the load and the RAM data is stored
    pre-interleaved: odd rows have the 32-bit halves of each 64-bit TMEM word
    swapped, i.e. 16-bit texel x is stored at x ^ 2.
    """
    need(len(data) >= width * height * 2, "RGBA16 texture data is truncated")
    need(not odd_lines_swapped or width % 4 == 0, "interleaved RGBA16 rows need 64-bit width")
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        swap = 2 if odd_lines_swapped and y & 1 else 0
        for x in range(width):
            at = (y * width + (x ^ swap)) * 2
            value = int.from_bytes(data[at:at + 2], "big")
            r, g, b, a = (value >> 11) & 31, (value >> 6) & 31, (value >> 1) & 31, value & 1
            rows.extend(((r << 3) | (r >> 2), (g << 3) | (g >> 2),
                         (b << 3) | (b >> 2), 255 if a else 0))
    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = binascii.crc32(payload, binascii.crc32(kind)) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


def need(ok: bool, message: str) -> None:
    if not ok:
        raise ModelError(message)


def parse_hand_state(value: str) -> int:
    """Accept the source-backed ordinary-idle alias or a numeric mask."""
    if value.casefold() == "idle":
        return 1
    try:
        result = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("hand state must be 'idle' or a numeric mask") from exc
    if not 0 <= result <= 0xFFFF:
        raise argparse.ArgumentTypeError("hand state must be a 16-bit value")
    return result


def be32(data: bytes, at: int) -> int:
    need(0 <= at <= len(data) - 4, f"u32 out of bounds at {at:#x}")
    return int.from_bytes(data[at:at + 4], "big")


def normalize_rom(raw: bytes) -> tuple[bytes, str]:
    need(len(raw) == EXPECTED_SIZE, f"expected 32 MiB ROM, got {len(raw)} bytes")
    magic = raw[:4]
    if magic == bytes.fromhex("80371240"):
        return raw, "z64 / big-endian"
    if magic == bytes.fromhex("37804012"):
        out = bytearray(len(raw))
        out[0::2] = raw[1::2]
        out[1::2] = raw[0::2]
        return bytes(out), "v64 / 16-bit byte-swapped"
    if magic == bytes.fromhex("40123780"):
        out = bytearray(len(raw))
        out[0::4] = raw[3::4]
        out[1::4] = raw[2::4]
        out[2::4] = raw[1::4]
        out[3::4] = raw[0::4]
        return bytes(out), "n64 / 32-bit byte-swapped"
    raise ModelError(f"unrecognized N64 ROM header {magic.hex()}")


def identify_rom(path: Path) -> tuple[bytes, dict]:
    raw = path.read_bytes()
    rom, byte_order = normalize_rom(raw)
    sys.path.insert(0, str(REPO_ROOT / ".deps"))
    try:
        import xxhash
    except ImportError as exc:
        raise ModelError("install requirements.txt into .deps to check Recompiled XXH3 identity") from exc
    name = rom[0x20:0x34].decode("ascii", errors="replace").rstrip()
    code = rom[0x3B:0x3F].decode("ascii", errors="replace")
    identity = xxhash.xxh3_64_intdigest(rom)
    info = {
        "size": len(raw), "byte_order": byte_order,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_sha1": hashlib.sha1(raw).hexdigest(),
        "normalized_sha256": hashlib.sha256(rom).hexdigest(),
        "normalized_sha1": hashlib.sha1(rom).hexdigest(),
        "internal_name": name, "game_code": code,
        "region_byte": f"0x{rom[0x3E]:02X}", "version": rom[0x3F],
        "xxh3_64_normalized": f"0x{identity:016x}",
        "recompiled_match": identity == EXPECTED_XXH3,
    }
    need(code == "NDOE" and rom[0x3E] == 0x45 and rom[0x3F] == 0,
         f"not DK64 US revision 0: {code=}, region={rom[0x3E]:#x}, version={rom[0x3F]}")
    need(identity == EXPECTED_XXH3,
         f"Recompiled identity mismatch: {identity:#018x} != {EXPECTED_XXH3:#018x}")
    return rom, info


def extract_entry(rom: bytes, table_id: int = TABLE, index: int = ENTRY) -> tuple[bytes, dict]:
    need(0 <= table_id < 32 and index >= 0, "invalid table/index")
    table_count = be32(rom, POINTER_BASE + 0x80 + table_id * 4)
    need(index < table_count, f"table {table_id} has {table_count} entries")
    table = POINTER_BASE + be32(rom, POINTER_BASE + table_id * 4)
    need(0 <= table <= len(rom) - (table_count + 1) * 4, "pointer table out of ROM")
    start_raw = be32(rom, table + index * 4)
    end_raw = be32(rom, table + (index + 1) * 4)
    start = POINTER_BASE + (start_raw & 0x7FFFFFFF)
    end = POINTER_BASE + (end_raw & 0x7FFFFFFF)
    need(0 <= start < end <= len(rom), f"entry bounds invalid: {start:#x}..{end:#x}")
    packed = rom[start:end]
    gzip = packed[:2] == b"\x1f\x8b"
    if gzip:
        try:
            stream = zlib.decompressobj(31)
            data = stream.decompress(packed)
            data += stream.flush()
        except zlib.error as exc:
            raise ModelError(f"gzip failed for table {table_id} entry {index}: {exc}") from exc
        need(stream.eof, "gzip stream did not terminate")
        # The verified DK entry has one nonzero byte after the gzip member but
        # before the next table pointer. Keep that padding visible in metadata.
        trailing = stream.unused_data
        need(len(trailing) <= 16, f"excessive trailing data after gzip: {len(trailing)} bytes")
    else:
        data = bytes(packed)
        trailing = b""
    need(bool(data), "empty extracted entry")
    return data, {
        "table": table_id, "entry": index, "table_offset": table,
        "table_count": table_count, "start": start, "end": end,
        "compressed_size": len(packed), "gzip": gzip,
        "gzip_trailing_bytes": trailing.hex(),
        "decompressed_size": len(data),
        "decompressed_sha256": hashlib.sha256(data).hexdigest(),
    }


@dataclass(frozen=True)
class Bone:
    parent: int
    local: int
    master: int
    translation: tuple[float, float, float]
    accumulated: tuple[float, float, float]


@dataclass
class Actor:
    data: bytes
    base_address: int
    vertices_start: int
    vertices_end: int
    dl_start: int
    dl_end: int
    root_offsets: list[int]
    bone_start: int
    bones: list[Bone]
    dynamic_texture_offset: int
    header: dict

    @property
    def vertex_count(self) -> int:
        return (self.vertices_end - self.vertices_start) // 16

    def vertex_position(self, index: int, bone_index: int) -> tuple[float, float, float]:
        need(0 <= index < self.vertex_count, f"vertex index {index} out of bounds")
        need(0 <= bone_index < len(self.bones), f"bone index {bone_index} out of bounds")
        at = self.vertices_start + index * 16
        xyz = struct.unpack_from(">hhh", self.data, at)
        move = self.bones[bone_index].accumulated
        result = tuple(float(xyz[i]) + move[i] for i in range(3))
        need(all(math.isfinite(x) for x in result), f"nonfinite positioned vertex {index}")
        return result


def parse_actor(data: bytes) -> Actor:
    need(len(data) >= 0x28, "actor header truncated")
    base = be32(data, 0)
    def off(addr: int) -> int:
        pos = addr - base + 0x28
        need(0 <= pos < len(data), f"actor pointer {addr:#x} out of bounds")
        return pos
    dl_end = off(be32(data, 4))
    bone_start = off(be32(data, 8))
    dynamic = off(be32(data, 0x10))
    bone_count, part_count = data[0x20], data[0x21]
    need(0 < bone_count <= 128 and 0 < part_count <= 64, "implausible bone/part count")
    need(dl_end + part_count * 4 <= len(data), "display-list pointer array truncated")
    roots = [off(be32(data, dl_end + 4 * i)) for i in range(part_count)]
    dl_start = min(roots)
    need(0x28 < dl_start < dl_end and (dl_end - dl_start) % 8 == 0,
         "invalid display-list block")
    need((dl_start - 0x28) % 16 == 0, "vertex block is not 16-byte aligned")
    need(bone_start >= dl_end + 4 * part_count, "bone table overlaps display-list pointers")
    need(bone_start + bone_count * 16 <= len(data), "bone table truncated")
    need(dynamic >= bone_start + bone_count * 16, "dynamic texture section overlaps bones")
    need(all(dl_start <= r < dl_end and (r - dl_start) % 8 == 0 for r in roots),
         "display-list root out of block")
    bones = []
    accumulated: dict[int, tuple[float, float, float]] = {}
    masters = set()
    for row in range(bone_count):
        pos = bone_start + row * 16
        parent, local, master = data[pos:pos + 3]
        move = struct.unpack_from(">fff", data, pos + 4)
        need(all(math.isfinite(v) for v in move), f"nonfinite bone translation {row}")
        need(local == row, f"bone local index {local} differs from row {row}")
        need(master < bone_count and master not in masters, f"invalid/duplicate bone master {master}")
        masters.add(master)
        if parent == 0xFF:
            parent_move = (0.0, 0.0, 0.0)
        else:
            need(parent in accumulated, f"bone {row} parent {parent} not yet defined")
            parent_move = accumulated[parent]
        total = tuple(move[i] + parent_move[i] for i in range(3))
        accumulated[local] = total
        bones.append(Bone(parent, local, master, move, total))
    header = {
        "base_address": f"0x{base:08x}",
        "pointer_array_address": f"0x{be32(data, 4):08x}",
        "bone_address": f"0x{be32(data, 8):08x}",
        "header_0xc": f"0x{be32(data, 0xC):08x}",
        "dynamic_texture_address": f"0x{be32(data, 0x10):08x}",
        "bone_count": bone_count, "part_count": part_count,
        "vertex_block": [0x28, dl_start], "display_list_block": [dl_start, dl_end],
        "pointer_array": [dl_end, dl_end + 4 * part_count],
        "bone_table": [bone_start, bone_start + 16 * bone_count],
        "dynamic_texture_offset": dynamic,
        "root_offsets": roots,
    }
    return Actor(data, base, 0x28, dl_start, dl_start, dl_end,
                 roots, bone_start, bones, dynamic, header)


@dataclass(frozen=True)
class VertexRef:
    source: int
    bone: int
    attribute_mode: str
    texture_gen: bool = False
    texture_gen_linear: bool = False


@dataclass(frozen=True)
class TextureUse:
    image_address: int
    texture_index: int | None
    dynamic_slot: int | None
    fmt: int
    size: int
    width: int
    height: int
    scale_s: int
    scale_t: int
    wrap_s: int
    wrap_t: int
    shift_s: int
    shift_t: int
    tile: int
    lod_level: int
    tile_uls: int = 0
    tile_ult: int = 0
    texture_filter: int | None = None  # None: no G_SETOTHERMODE_H TEXTFILT seen
    # G_LOADBLOCK dxt=0: RAM image is pre-interleaved (odd-line word swap).
    odd_lines_swapped: bool = False
    # Render tile size last set by the segment-5 gDPSetHilite1Tile runtime DL,
    # so tile_uls/tile_ult are placeholders for a camera/light-dependent origin.
    hilite_origin: bool = False
    # Size of the image the render tile wraps over (G_SETTILE masks). It differs from the
    # tile size (width/height, which also drives the hilite origin) for some Chunky/Lanky
    # hilite tiles (32x16 / 16x16 windows over a fully loaded 32x32 image). 0: same as tile.
    image_width: int = 0
    image_height: int = 0

    @property
    def sample_width(self) -> int:
        return self.image_width or self.width

    @property
    def sample_height(self) -> int:
        return self.image_height or self.height


@dataclass
class Mesh:
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    refs: list[VertexRef] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    colors: list[tuple[float, float, float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)
    parts: list[list[tuple[int, int, int]]] = field(default_factory=list)
    triangle_texture_uses: list[TextureUse | None] = field(default_factory=list)
    triangle_texgen_modes: list[tuple[bool, bool]] = field(default_factory=list)
    # True when the RDP combiner is shade-only at this triangle (no texture sampled).
    triangle_shade_only: list[bool] = field(default_factory=list)
    # True when the RDP Z mode is decal (G_SETOTHERMODE_L ZMODE = 3) at this triangle.
    triangle_z_decal: list[bool] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def decode_actor_mesh(actor: Actor, hand_state: int = 0) -> Mesh:
    need(0 <= hand_state <= 0xFFFF, "hand state must be a 16-bit value")
    mesh = Mesh()
    key_to_output: dict[VertexRef, int] = {}
    source_bones: dict[int, set[int]] = collections.defaultdict(set)
    opcodes = collections.Counter()
    unsupported: dict[int, str] = {}
    external_calls = collections.Counter()
    conditionals = []
    degenerate = 0
    duplicate = 0
    triangle_keys = set()
    triangle_commands = []
    all_referenced = set()
    all_loaded = set()
    referenced_source_bones: dict[int, set[int]] = collections.defaultdict(set)
    attribute_load_counts = collections.Counter()
    matrix_commands_by_bone = collections.Counter()
    vertex_loads_by_bone = collections.Counter()
    vertex_instances_by_bone = collections.Counter()
    geometry_mode = G_LIGHTING  # playable DK draw setup sets this from object flag 0x1000
    geometry_mode_commands = []
    # Track only the texture commands exercised by this actor's display lists.
    texture_image = 0
    texture_fmt = 0
    texture_size = 0
    texture_width = 1
    texture_scale_s = texture_scale_t = 0xFFFF
    texture_enabled = False
    shade_only = False
    z_decal = False
    texture_tile = texture_lod = 0
    texture_filter: int | None = None
    tiles: dict[int, dict] = collections.defaultdict(dict)
    tile_sizes: dict[int, tuple[int, int, int, int]] = {}
    hilite_tiles: set[int] = set()  # tiles last sized by the segment-5 hilite DL
    texture_loaded = False
    load_texel_count = 0
    load_dxt = 0
    tlut_seen = False
    dynamic_textures = parse_dynamic_textures(actor)
    bone_ids = set()
    cache: list[VertexRef | None] = [None] * SLOT_COUNT
    active_bone: int | None = None
    max_steps = (actor.dl_end - actor.dl_start) // 8 * 8
    steps = 0

    def output_index(ref: VertexRef) -> int:
        if ref not in key_to_output:
            key_to_output[ref] = len(mesh.positions)
            mesh.positions.append(actor.vertex_position(ref.source, ref.bone))
            mesh.refs.append(ref)
            at = actor.vertices_start + ref.source * 16 + 12
            trailing = actor.data[at:at + 4]
            if ref.attribute_mode == "normal":
                mesh.normals.append(decode_n64_normal(trailing[:3]))
            elif ref.attribute_mode == "color":
                mesh.colors.append(tuple(value / 255.0 for value in trailing))
            else:
                raise ModelError(f"unknown vertex attribute mode {ref.attribute_mode!r}")
        return key_to_output[ref]

    for root in actor.root_offsets:
        part_triangles: list[tuple[int, int, int]] = []
        # Top-level actor parts inherit no vertex cache from another part.
        cache = [None] * SLOT_COUNT
        active_bone = None
        pc = root
        stack: list[tuple[int, int]] = []
        active_targets = {root}
        while True:
            steps += 1
            need(steps <= max_steps, "display-list step limit / possible cycle")
            need(actor.dl_start <= pc <= actor.dl_end - 8 and (pc - actor.dl_start) % 8 == 0,
                 f"display-list PC {pc:#x} out of bounds")
            command = actor.data[pc:pc + 8]
            op = command[0]
            opcodes[op] += 1
            word = int.from_bytes(command[4:8], "big")
            if op == 0xD9:  # G_GEOMETRYMODE: state applies when G_VTX loads a cache entry.
                clear_bits = (~int.from_bytes(command[:4], "big")) & 0xFFFFFF
                geometry_mode = (geometry_mode & ~clear_bits) | word
                geometry_mode_commands.append({
                    "offset": f"0x{pc:04x}",
                    "clear_bits": f"0x{clear_bits:06x}",
                    "set_bits": f"0x{word:08x}",
                    "lighting": bool(geometry_mode & G_LIGHTING),
                })
            elif op == 0xD7:  # G_TEXTURE: F3DEX2 texture enable/tile/level and 16.16 scales.
                texture_lod = (int.from_bytes(command[:4], "big") >> 11) & 7
                texture_tile = (int.from_bytes(command[:4], "big") >> 8) & 7
                texture_enabled = bool(int.from_bytes(command[:4], "big") & 2)
                texture_scale_s, texture_scale_t = (int.from_bytes(command[4:6], "big"),
                                                    int.from_bytes(command[6:8], "big"))
            elif op == 0xE3:  # F3DEX2 G_SETOTHERMODE_H: w0 holds 32-shift-len and len-1.
                length = command[3] + 1
                shift_amount = 32 - command[2] - length
                if shift_amount <= G_MDSFT_TEXTFILT and shift_amount + length >= G_MDSFT_TEXTFILT + 2:
                    texture_filter = (word >> G_MDSFT_TEXTFILT) & 3
            elif op == 0xE2:  # F3DEX2 G_SETOTHERMODE_L: render mode holds the Z mode (bits 10-11).
                length = command[3] + 1
                shift_amount = 32 - command[2] - length
                if shift_amount <= G_MDSFT_ZMODE and shift_amount + length >= G_MDSFT_ZMODE + 2:
                    z_decal = ((word >> G_MDSFT_ZMODE) & 3) == ZMODE_DECAL
            elif op == 0xFC:  # G_SETCOMBINE
                shade_only = (int.from_bytes(command[:4], "big") & 0xFFFFFF, word) == G_CC_SHADE_ONLY
            elif op == 0xFD:  # G_SETTIMG; actor loader resolves segment-zero IDs through table 25.
                w0 = int.from_bytes(command[:4], "big")
                texture_fmt = (w0 >> 21) & 7
                texture_size = (w0 >> 19) & 3
                texture_width = (w0 & 0xFFF) + 1
                texture_image = word
                texture_loaded = False
                load_texel_count = 0
            elif op == 0xF5:  # G_SETTILE: remember address mode, shifts, and format per tile.
                w0 = int.from_bytes(command[:4], "big")
                tile = (word >> 24) & 7
                tiles[tile] = {
                    "fmt": (w0 >> 21) & 7, "size": (w0 >> 19) & 3,
                    "wrap_t": (word >> 18) & 3, "mask_t": (word >> 14) & 15,
                    "shift_t": (word >> 10) & 15, "wrap_s": (word >> 8) & 3,
                    "mask_s": (word >> 4) & 15, "shift_s": word & 15,
                }
            elif op == 0xF2:  # G_SETTILESIZE in quarter-texel coordinates.
                tile = (word >> 24) & 7
                hilite_tiles.discard(tile)
                w0 = int.from_bytes(command[:4], "big")
                lrs = (word >> 12) & 0xFFF
                lrt = word & 0xFFF
                uls, ult = (w0 >> 12) & 0xFFF, w0 & 0xFFF
                tile_sizes[tile] = ((lrs - uls) // 4 + 1, (lrt - ult) // 4 + 1,
                                    uls, ult)
            elif op == 0xF3:  # G_LOADBLOCK; records that the current G_SETTIMG was loaded.
                texture_loaded = True
                load_texel_count = ((word >> 12) & 0xFFF) + 1
                load_dxt = word & 0xFFF
            elif op == 0xF0:  # G_LOADTLUT
                tlut_seen = True
            elif op == 0xDA:  # G_MTX: DK actor segment 4 bone matrices, 0x40 bytes each.
                need(command[3] == 3, f"unsupported G_MTX flags {command[3]:#x} at {pc:#x}")
                need(word >> 24 == 4 and (word & 0xFFFFFF) % 0x40 == 0,
                     f"unsupported matrix address {word:#x} at {pc:#x}")
                active_bone = (word & 0xFFFFFF) // 0x40
                need(active_bone < len(actor.bones), f"bone {active_bone} out of range")
                bone_ids.add(active_bone)
                matrix_commands_by_bone[active_bone] += 1
            elif op == 0x01:  # G_VTX: n and cache end follow dk64_lib G_VTX.
                count = (int.from_bytes(command[1:3], "big") >> 4) & 0xFF
                need(command[3] % 2 == 0, f"odd vertex-cache end at {pc:#x}")
                end_slot = command[3] // 2
                first_slot = end_slot - count
                need(0 < count <= SLOT_COUNT and 0 <= first_slot < end_slot <= SLOT_COUNT,
                     f"vertex-cache load invalid at {pc:#x}: n={count}, end={end_slot}")
                need(word >> 24 == 3 and (word & 0xFFFFFF) % 16 == 0,
                     f"unsupported G_VTX segment/alignment {word:#x} at {pc:#x}")
                first_vertex = (word & 0xFFFFFF) // 16
                need(first_vertex + count <= actor.vertex_count,
                     f"G_VTX global vertex range out of bounds at {pc:#x}")
                need(active_bone is not None, f"G_VTX before G_MTX at {pc:#x}")
                vertex_loads_by_bone[active_bone] += 1
                vertex_instances_by_bone[active_bone] += count
                for i in range(count):
                    mode = "normal" if geometry_mode & G_LIGHTING else "color"
                    ref = VertexRef(first_vertex + i, active_bone, mode,
                                    bool(geometry_mode & 0x00040000),
                                    bool(geometry_mode & 0x00080000))
                    cache[first_slot + i] = ref
                    all_loaded.add(ref.source)
                    source_bones[ref.source].add(ref.bone)
                    attribute_load_counts[mode] += 1
            elif op in (0x05, 0x06):  # G_TRI1 and G_TRI2, encoded cache indices / 2.
                triples = [command[1:4]]
                if op == 0x06:
                    triples.append(command[5:8])
                for raw_indices in triples:
                    need(all(i % 2 == 0 for i in raw_indices),
                         f"odd triangle cache index at {pc:#x}")
                    slots = tuple(i // 2 for i in raw_indices)
                    need(all(i < SLOT_COUNT and cache[i] is not None for i in slots),
                         f"triangle uses unloaded cache slot {slots} at {pc:#x}")
                    refs = tuple(cache[i] for i in slots)
                    texgen_modes = {(ref.texture_gen, ref.texture_gen_linear) for ref in refs}
                    need(len(texgen_modes) == 1,
                         f"triangle at {pc:#x} mixes texture-generation vertex states")
                    all_referenced.update(ref.source for ref in refs)
                    for ref in refs:
                        referenced_source_bones[ref.source].add(ref.bone)
                    if len(set(refs)) < 3:
                        degenerate += 1
                        continue
                    tri = tuple(output_index(ref) for ref in refs)
                    key = tuple(sorted(tri))
                    if key in triangle_keys:
                        duplicate += 1
                    triangle_keys.add(key)
                    mesh.triangles.append(tri)
                    mesh.triangle_texgen_modes.append(next(iter(texgen_modes)))
                    active_tile = texture_tile
                    desc = tiles.get(active_tile, {})
                    tw, th, tile_uls, tile_ult = tile_sizes.get(active_tile, (0, 0, 0, 0))
                    # When this actor omits G_SETTILESIZE, its G_SETTILE masks and
                    # the 1024-texel RGBA16 load establish the 32x32 base level.
                    if not tw and desc.get("mask_s") and desc.get("mask_t"):
                        inferred_w, inferred_h = 1 << desc["mask_s"], 1 << desc["mask_t"]
                        if inferred_w * inferred_h == load_texel_count:
                            tw, th = inferred_w, inferred_h
                    image_segment = texture_image >> 24
                    dynamic_slot = image_segment if image_segment in dynamic_textures else None
                    texture_index = (texture_image & 0xFFFFFF) if image_segment == 0 else None
                    use = None
                    image_w = (1 << desc["mask_s"]) if desc.get("mask_s") else tw
                    image_h = (1 << desc["mask_t"]) if desc.get("mask_t") else th
                    if texture_enabled and texture_loaded and tw and th:
                        use = TextureUse(texture_image, texture_index, dynamic_slot,
                                         desc.get("fmt", texture_fmt), desc.get("size", texture_size),
                                         tw, th, texture_scale_s, texture_scale_t,
                                         desc.get("wrap_s", 0), desc.get("wrap_t", 0),
                                         desc.get("shift_s", 0), desc.get("shift_t", 0),
                                         active_tile, texture_lod, tile_uls, tile_ult,
                                         texture_filter, load_dxt == 0,
                                         active_tile in hilite_tiles, image_w, image_h)
                    mesh.triangle_texture_uses.append(use)
                    mesh.triangle_shade_only.append(shade_only)
                    mesh.triangle_z_decal.append(z_decal)
                    triangle_commands.append(pc)
                    part_triangles.append(tri)
            elif op == 0xDE:  # G_DL: call or no-push branch.
                push = command[1] == 0
                segment, offset = word >> 24, word & 0xFFFFFF
                if segment == 5 and offset == 0 and push:
                    # Decomp code_2F550 sets segment 5 to a two-command hilite DL.
                    external_calls["segment5_hilite_state"] += 1
                    hilite_tiles.add(0)  # gDPSetHilite1Tile(G_TX_RENDERTILE, ...)
                elif segment in (7, 8) and offset == 0 and not push:
                    # Runtime func_global_asm_80614C38 selects next command when
                    # actor->unk146 bit (segment - 7) is set. The player's
                    # initialized state is 0 (decomp code_CC800.c), not the
                    # generic spawn default -1 (code_7CA80.c).
                    marker = next((p for p in range(pc + 8, actor.dl_end, 8)
                                   if actor.data[p] == 0 and be32(actor.data, p + 4) == segment), None)
                    need(marker is not None, f"conditional marker for segment {segment} missing")
                    include = bool((hand_state >> (segment - 7)) & 1)
                    possible_triangles = sum(
                        1 if actor.data[p] == 0x05 else 2 if actor.data[p] == 0x06 else 0
                        for p in range(pc + 8, marker, 8)
                    )
                    conditionals.append({"segment": segment, "branch_at": pc,
                                         "included_range": [pc + 8, marker],
                                         "marker_at": marker,
                                         "potential_triangles": possible_triangles,
                                         "selected": "include" if include else "skip"})
                    if not include:
                        pc = marker
                        continue
                else:
                    # A local actor segment-3 target may be used in other files;
                    # it is supported only if it points into this DL block.
                    need(segment == 3, f"unsupported geometry-affecting G_DL target {word:#x} at {pc:#x}")
                    target = actor.vertices_start + offset
                    need(actor.dl_start <= target < actor.dl_end and (target - actor.dl_start) % 8 == 0,
                         f"invalid local G_DL target {word:#x} at {pc:#x}")
                    need(target not in active_targets, f"display-list cycle to {target:#x}")
                    if push:
                        stack.append((pc + 8, target))
                        active_targets.add(target)
                        pc = target
                        continue
                    pc = target
                    continue
            elif op == 0xDF:  # G_ENDDL
                if stack:
                    pc, completed_target = stack.pop()
                    active_targets.remove(completed_target)
                    continue
                break
            elif op in KNOWN_STATE:
                pass
            else:
                category = "geometry-affecting" if op in GEOMETRY_AFFECTING else "unknown"
                unsupported[op] = category
            pc += 8
        mesh.parts.append(part_triangles)
    assigned = {i for i, bones in source_bones.items() if bones}
    bone_geometry = {}
    for bone in range(len(actor.bones)):
        indices = [i for i, ref in enumerate(mesh.refs) if ref.bone == bone]
        if indices:
            coords = [mesh.positions[i] for i in indices]
            bone_geometry[str(bone)] = {
                "output_vertices": len(indices),
                "bounding_box": [[min(p[k] for p in coords) for k in range(3)],
                                 [max(p[k] for p in coords) for k in range(3)]],
                "triangles_touching": sum(any(mesh.refs[i].bone == bone for i in tri)
                                          for tri in mesh.triangles),
            }
    mesh.stats = {
        "original_vertex_count": actor.vertex_count,
        "loaded_source_vertices": len(all_loaded),
        "referenced_source_vertices": len(all_referenced),
        "assigned_source_vertices": len(assigned),
        "unassigned_source_vertices": actor.vertex_count - len(assigned),
        "conflicting_bone_sources": {str(i): sorted(b) for i, b in source_bones.items() if len(b) > 1},
        "conflicting_referenced_bone_sources": {
            str(i): sorted(b) for i, b in referenced_source_bones.items() if len(b) > 1},
        "bone_ids_encountered": sorted(bone_ids),
        "g_mtx_command_count": opcodes[0xDA],
        "g_mtx_commands_by_bone": {str(k): v for k, v in sorted(matrix_commands_by_bone.items())},
        "g_popmtx_command_count": opcodes[0xD8],
        "g_vtx_loads_by_bone": {str(k): v for k, v in sorted(vertex_loads_by_bone.items())},
        "g_vtx_vertex_instances_by_bone": {
            str(k): v for k, v in sorted(vertex_instances_by_bone.items())},
        "loaded_source_vertices_by_bone": {
            str(bone): sum(bone in bones for bones in source_bones.values())
            for bone in sorted(bone_ids)},
        "referenced_source_vertices_by_bone": {
            str(bone): sum(bone in bones for bones in referenced_source_bones.values())
            for bone in range(len(actor.bones)) if any(
                bone in bones for bones in referenced_source_bones.values())},
        "invalid_references": 0,
        "output_vertex_count": len(mesh.positions),
        "triangle_count": len(mesh.triangles),
        "degenerate_triangles_skipped": degenerate,
        "duplicate_triangles_detected": duplicate,
        "part_triangle_counts": [len(p) for p in mesh.parts],
        "opcode_counts": {f"0x{k:02X}": v for k, v in sorted(opcodes.items())},
        "unsupported_opcodes": {f"0x{k:02X}": v for k, v in sorted(unsupported.items())},
        "external_calls": dict(external_calls),
        "conditional_parts": conditionals,
        "hand_state": hand_state,
        "vertex_attribute_load_counts": dict(attribute_load_counts),
        "vertex_attribute_modes": sorted({ref.attribute_mode for ref in mesh.refs}),
        "geometry_mode_initial": f"0x{G_LIGHTING:08x}",
        "geometry_mode_commands": geometry_mode_commands,
        "texture_loaded": texture_loaded,
        "tlut_command_seen": tlut_seen,
        "conditional_triangle_counts": {
            str(c["segment"]): sum(c["included_range"][0] <= pc < c["included_range"][1]
                                   for pc in triangle_commands)
            for c in conditionals
        },
        "bone_geometry": bone_geometry,
    }
    need(bool(mesh.triangles), "no triangles recovered")
    need(not any(v == "geometry-affecting" for v in unsupported.values()),
         f"unsupported geometry-affecting opcodes: {unsupported}")
    return mesh


def export_gltf(mesh: Mesh, out: Path, node_name: str = "Donkey Kong (static actor pose)") -> dict:
    need(bool(mesh.positions) and bool(mesh.triangles), "empty mesh")
    modes = {ref.attribute_mode for ref in mesh.refs}
    need(len(modes) == 1, f"mixed vertex formats need state-split primitives: {sorted(modes)}")
    attribute_mode = next(iter(modes))
    need((attribute_mode == "normal" and len(mesh.normals) == len(mesh.positions)) or
         (attribute_mode == "color" and len(mesh.colors) == len(mesh.positions)),
         "vertex attribute count does not match positions")
    out.parent.mkdir(parents=True, exist_ok=True)
    # DK actor XYZ and glTF are both represented Y-up; preserve winding and
    # native units pending visual confirmation. No arbitrary pose rotation.
    positions = b"".join(struct.pack("<fff", *p) for p in mesh.positions)
    vertex_attribute = (mesh.normals if attribute_mode == "normal" else mesh.colors)
    attribute_bytes = b"".join(struct.pack("<fff" if attribute_mode == "normal" else "<ffff", *v)
                                for v in vertex_attribute)
    indices = b"".join(struct.pack("<III", *tri) for tri in mesh.triangles)
    pad = (-len(positions)) % 4
    attr_offset = len(positions) + pad
    pad_after_attribute = (-len(attribute_bytes)) % 4
    index_offset = attr_offset + len(attribute_bytes) + pad_after_attribute
    blob = (positions + b"\x00" * pad + attribute_bytes +
            b"\x00" * pad_after_attribute + indices)
    min_xyz = [min(p[i] for p in mesh.positions) for i in range(3)]
    max_xyz = [max(p[i] for p in mesh.positions) for i in range(3)]
    gltf = {
        "asset": {"version": "2.0", "generator": "DK64 Forge Phase 1 static DK experiment"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": node_name}],
        "meshes": [{"name": "DK actor table 5 entry 3", "primitives": [{
            "attributes": {"POSITION": 0,
                           ("NORMAL" if attribute_mode == "normal" else "COLOR_0"): 1},
            "indices": 2, "material": 0, "mode": 4,
        }]}],
        "materials": [{"name": "Untextured geometry proof", "doubleSided": True,
                       "pbrMetallicRoughness": {"baseColorFactor": [0.55, 0.32, 0.18, 1],
                                                "metallicFactor": 0, "roughnessFactor": 1}}],
        "buffers": [{"uri": out.with_suffix(".bin").name, "byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": attr_offset, "byteLength": len(attribute_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": index_offset, "byteLength": len(indices), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(mesh.positions),
             "type": "VEC3", "min": min_xyz, "max": max_xyz},
            {"bufferView": 1, "componentType": 5126, "count": len(vertex_attribute),
             "type": "VEC3" if attribute_mode == "normal" else "VEC4"},
            {"bufferView": 2, "componentType": 5125, "count": len(mesh.triangles) * 3,
             "type": "SCALAR", "min": [0], "max": [len(mesh.positions) - 1]},
        ],
    }
    out.with_suffix(".bin").write_bytes(blob)
    out.write_text(json.dumps(gltf, indent=2) + "\n", encoding="utf-8")
    return gltf


def actor_raw_normal(actor: Actor, ref: VertexRef) -> tuple[float, float, float]:
    """RSP input normal: signed bytes / 127, not renormalized (RT64 RSPProcessCS)."""
    at = actor.vertices_start + ref.source * 16 + 12
    return tuple(value / 127.0 for value in struct.unpack_from("bbb", actor.data, at))


def with_hilite_origin(use: TextureUse, camera: dict = TEXGEN_BAKE_CAMERA) -> TextureUse:
    """Resolve the gDPSetHilite1Tile render-tile origin for this camera."""
    if not use.hilite_origin:
        return use
    uls, ult = dk64_hilite_upper_left(camera, use.width, use.height)
    return replace(use, tile_uls=uls, tile_ult=ult)


def dk64_hilite_upper_left(camera: dict, width: int, height: int) -> tuple[int, int]:
    return texgen.hilite_upper_left(camera["eye"], camera["at"], camera["up"],
                                       HILITE_LIGHTS[0], width, height)


def texgen_uv(normal: tuple[float, float, float], use: TextureUse, linear: bool,
              camera: dict = TEXGEN_BAKE_CAMERA) -> tuple[float, float]:
    """Spherical/linear G_TEXTURE_GEN UV for one world-space normal and camera."""
    right, up, _ = texgen.lookat_basis(camera["eye"], camera["at"], camera["up"])
    s, t = texgen.texgen_texels(np.asarray([normal], dtype=np.float64), right, up,
                                   use.scale_s, use.scale_t, linear)[0]
    return tile_texels_to_uv(float(s), float(t), use)


def export_textured_gltf(mesh: Mesh, actor: Actor, rom: bytes, out: Path,
                         node_name: str = "Donkey Kong (canonical idle, first dynamic frames)") -> dict:
    """Export stored UVs, and G_TEXTURE_GEN UVs baked for TEXGEN_BAKE_CAMERA."""
    need(len(mesh.triangle_texture_uses) == len(mesh.triangles),
         "texture state count does not match triangle count")
    dynamic = parse_dynamic_textures(actor)
    uses = []
    resolved = []
    texgen_modes = mesh.triangle_texgen_modes
    need(len(texgen_modes) == len(mesh.triangles), "texture-generation state count mismatch")
    shade_only = mesh.triangle_shade_only or [False] * len(mesh.triangles)
    need(len(shade_only) == len(mesh.triangles), "shade-only state count mismatch")
    z_decal = mesh.triangle_z_decal or [False] * len(mesh.triangles)
    need(len(z_decal) == len(mesh.triangles), "z-mode state count mismatch")
    # The third element marks RDP decal Z mode; it only adds a grouping/extras flag.
    texgen_modes = [((False, False) if flat else mode) + (decal,)
                    for mode, flat, decal in zip(texgen_modes, shade_only, z_decal)]
    for use, flat in zip(mesh.triangle_texture_uses, shade_only):
        if flat:
            # Combiner (0 - 0) * 0 + SHADE: an untextured, lit surface.
            uses.append(None)
            continue
        if use is None:
            # The first 28 E64 triangles precede an explicit tile-size command.
            # Their 1024-texel load and mask-5 descriptor identify the same base image.
            use = TextureUse(0xE64, 0xE64, None, 0, 2, 32, 32, 0x0800, 0x0800,
                             0, 0, 0, 0, 0, 0)
        if use.dynamic_slot is not None:
            frames = dynamic.get(use.dynamic_slot, ())
            need(bool(frames), f"dynamic texture slot {use.dynamic_slot:#x} has no table entry")
            index = frames[0]  # Randomizer model_port.py uses this same deterministic static fallback.
            use = replace(use, texture_index=index)
        use = with_hilite_origin(use)
        need(use.texture_index is not None and use.texture_index < 0x10000,
             f"unresolved texture address {use.image_address:#x}")
        need((use.fmt, use.size) == (0, 2),
             f"canonical texture format is not RGBA16: {(use.fmt, use.size)}")
        need(use.width > 0 and use.height > 0, "canonical texture dimensions unresolved")
        uses.append(use)
        resolved.append(use.texture_index)

    group_keys = list(dict.fromkeys(zip(uses, texgen_modes)))
    unique_texture_ids = sorted(set(resolved))
    odd_lines_swapped: dict[int, bool] = {}
    for use in uses:
        if use is None:
            continue
        swapped = odd_lines_swapped.setdefault(use.texture_index, use.odd_lines_swapped)
        need(swapped == use.odd_lines_swapped,
             f"texture {use.texture_index:#x} is loaded both interleaved and linear")
    out.parent.mkdir(parents=True, exist_ok=True)
    texture_dir = out.parent / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)
    image_uri_by_id = {}
    texture_metadata = {}
    image_size_by_id: dict[int, tuple[int, int]] = {}
    for use in uses:
        if use is None:
            continue
        size = image_size_by_id.setdefault(use.texture_index, (use.sample_width, use.sample_height))
        need(size == (use.sample_width, use.sample_height),
             f"texture {use.texture_index:#x} is sampled over two different image sizes")
    for texture_id in unique_texture_ids:
        data, info = extract_entry(rom, table_id=25, index=texture_id)
        # RGBA16 base level at the tile's wrap-mask size (32x32 for most actor tiles). Some
        # table entries also carry precomputed mip levels after the base level.
        width, height = image_size_by_id[texture_id]
        need(len(data) >= width * height * 2, f"table 25 texture {texture_id:#x} lacks its base level")
        image_name = f"dk64_table25_{texture_id:04x}_rgba16_{width}x{height}.png"
        image_path = texture_dir / image_name
        image_path.write_bytes(rgba16_to_png(data[:width * height * 2], width, height,
                                             odd_lines_swapped[texture_id]))
        image_uri_by_id[texture_id] = image_path.relative_to(out.parent).as_posix()
        texture_metadata[str(texture_id)] = {
            "pointer_table": 25, "entry": texture_id, "format": "RGBA16",
            "dimensions": [width, height], "source_length": len(data), "gzip": info["gzip"],
            "decompressed_sha256": info["decompressed_sha256"],
            "base_level_bytes_used": width * height * 2,
            "odd_line_word_swap_undone": odd_lines_swapped[texture_id],
        }

    # Split vertices only at a texture/sampler/UV-state boundary. Source topology stays intact.
    positions, normals, texcoords, indices = [], [], [], []
    material_by_key = {key: i for i, key in enumerate(group_keys)}
    grouped_triangles: dict[tuple[TextureUse, tuple[bool, bool]], list[tuple[int, int, int]]] = collections.defaultdict(list)
    for tri, use, texgen in zip(mesh.triangles, uses, texgen_modes):
        grouped_triangles[(use, texgen)].append(tri)
    primitives = []
    vertex_cache = {}
    for use, texgen in group_keys:
        primitive_indices = []
        for tri in grouped_triangles[(use, texgen)]:
            for source_index in tri:
                key = (use, texgen, source_index)
                if key not in vertex_cache:
                    vertex_cache[key] = len(positions)
                    positions.append(mesh.positions[source_index])
                    normals.append(mesh.normals[source_index])
                    at = actor.vertices_start + mesh.refs[source_index].source * 16 + 8
                    raw_s, raw_t = struct.unpack_from(">hh", actor.data, at)
                    if use is None:
                        texcoords.append((0.0, 0.0))
                    elif texgen[0]:
                        # Bind-pose bones only translate, so model normals are world normals.
                        texcoords.append(texgen_uv(actor_raw_normal(actor, mesh.refs[source_index]),
                                                   use, texgen[1]))
                    else:
                        texcoords.append(actor_uv(raw_s, raw_t, use))
                primitive_indices.append(vertex_cache[key])
        attributes = {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}
        extras = None
        if texgen[0]:
            extras = {"dk64_texture_generation": "G_TEXTURE_GEN spherical normal projection",
                      "G_TEXTURE_GEN_LINEAR": bool(texgen[1]),
                      "source_texture_table": 25,
                      "source_texture_entry": use.texture_index,
                      "dynamic_slot": use.dynamic_slot,
                      "uv_status": "G_TEXTURE_GEN baked for a fixed front camera; "
                                   "view-dependent in game",
                      "texgen_bake_camera": {key: list(value)
                                             for key, value in TEXGEN_BAKE_CAMERA.items()},
                      "texgen_formula_evidence": "VERIFIED (RT64 TextureGen.hlsli, libultra lookathil.c)",
                      "texgen_camera_evidence": "DIAGNOSTIC ASSUMPTION",
                      "hilite_tile_origin": use.hilite_origin,
                      "hilite_light": list(HILITE_LIGHTS[0]) if use.hilite_origin else None,
                      # Enough tile state for a viewer to regenerate UVs for another camera.
                      "texgen_tile": {"scale": [use.scale_s, use.scale_t],
                                      "size": [use.width, use.height],
                                      "image_size": [use.sample_width, use.sample_height],
                                      "shift": [use.shift_s, use.shift_t],
                                      "upper_left_quarter_texels": [use.tile_uls, use.tile_ult],
                                      "texture_filter": use.texture_filter}}
        elif use is None:
            extras = {"dk64_untextured_shade": "G_SETCOMBINE (0 - 0) * 0 + SHADE",
                      "combiner_evidence": "VERIFIED (F3DEX2/RDP combiner encoding in the display list)",
                      "uv_status": "no texture is sampled; lit vertex colour only",
                      "color_status": "DIAGNOSTIC ASSUMPTION: rendered white, the actor lighting "
                                      "(ambient + directional colours) is not modelled"}
        elif use.hilite_origin:
            # Stored S/T, but the render tile was last sized by the segment-5
            # gDPSetHilite1Tile DL, so its origin is camera/light dependent in game.
            extras = {"dk64_stored_uv_hilite": "stored S/T with hilite tile origin",
                      "source_texture_table": 25,
                      "source_texture_entry": use.texture_index,
                      "dynamic_slot": use.dynamic_slot,
                      "uv_status": "stored S/T with hilite tile origin baked for a fixed "
                                   "front camera; view-dependent in game",
                      "texgen_bake_camera": {key: list(value)
                                             for key, value in TEXGEN_BAKE_CAMERA.items()},
                      "hilite_formula_evidence": "VERIFIED (same tile rule as Phase 1F/1G)",
                      "hilite_camera_evidence": "DIAGNOSTIC ASSUMPTION",
                      "hilite_tile": {"size": [use.width, use.height],
                                      "image_size": [use.sample_width, use.sample_height],
                                      "baked_upper_left_quarter_texels": [use.tile_uls, use.tile_ult],
                                      "texture_filter": use.texture_filter}}
        if texgen[2]:
            extras = dict(extras or {})
            extras["dk64_z_mode"] = "decal"
            extras["z_mode_evidence"] = "VERIFIED (G_SETOTHERMODE_L render mode ZMODE = 3 in the display list)"
        primitive = {"attributes": attributes, "indices": 3 + len(primitives),
                     "material": material_by_key[(use, texgen)], "mode": 4}
        if extras:
            primitive["extras"] = extras
        primitives.append(primitive)
        indices.append(primitive_indices)

    # Each primitive gets its own sampler and texture record so wrap behavior follows its tile.
    samplers, images, textures, materials = [], [], [], []
    image_index_by_uri = {}
    for texture_id in unique_texture_ids:
        uri = image_uri_by_id[texture_id]
        image_index_by_uri[uri] = len(images)
        images.append({"uri": uri, "name": f"DK64 table 25 entry {texture_id:#x}"})
    for use, texgen in group_keys:
        if use is None:
            materials.append({"name": "DK untextured shade-only (white, lighting not modelled)",
                              "doubleSided": True, "alphaMode": "OPAQUE",
                              "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1],
                                                       "metallicFactor": 0, "roughnessFactor": 1}})
            continue
        texture_id = use.texture_index
        mode = {0: 10497, 1: 33648, 2: 33071}
        need(use.wrap_s in mode and use.wrap_t in mode, "unsupported N64 tile address mode")
        samplers.append({"magFilter": 9729, "minFilter": 9987,
                         "wrapS": mode[use.wrap_s], "wrapT": mode[use.wrap_t]})
        textures.append({"sampler": len(samplers) - 1,
                         "source": image_index_by_uri[image_uri_by_id[texture_id]]})
        name = (f"DK texgen {texture_id:#04x} (front-camera bake)" if texgen[0] else
                f"DK texture {texture_id:#04x} s{use.scale_s:04x}_t{use.scale_t:04x}")
        # The generated-UV body surfaces are drawn opaque; stored-UV decals keep blending.
        materials.append({"name": name, "doubleSided": True,
                          "alphaMode": "OPAQUE" if texgen[0] else "BLEND",
                          "pbrMetallicRoughness": {
                              "baseColorTexture": {"index": len(textures) - 1},
                              "metallicFactor": 0, "roughnessFactor": 1}})

    def pack_rows(rows, fmt):
        return b"".join(struct.pack(fmt, *row) for row in rows)
    pos_bytes, nrm_bytes, uv_bytes = (pack_rows(positions, "<fff"), pack_rows(normals, "<fff"),
                                      pack_rows(texcoords, "<ff"))
    blob = bytearray()
    views, accessors = [], []
    for raw, target, typ, comp, count, bounds in (
        (pos_bytes, 34962, "VEC3", 5126, len(positions), True),
        (nrm_bytes, 34962, "VEC3", 5126, len(normals), False),
        (uv_bytes, 34962, "VEC2", 5126, len(texcoords), False),
    ):
        while len(blob) % 4: blob.append(0)
        offset = len(blob); blob.extend(raw)
        view = len(views); views.append({"buffer": 0, "byteOffset": offset,
                                         "byteLength": len(raw), "target": target})
        acc = {"bufferView": view, "componentType": comp, "count": count, "type": typ}
        if bounds:
            acc["min"] = [min(p[i] for p in positions) for i in range(3)]
            acc["max"] = [max(p[i] for p in positions) for i in range(3)]
        accessors.append(acc)
    for group in indices:
        while len(blob) % 4: blob.append(0)
        offset = len(blob); raw = struct.pack(f"<{len(group)}I", *group); blob.extend(raw)
        view = len(views); views.append({"buffer": 0, "byteOffset": offset,
                                         "byteLength": len(raw), "target": 34963})
        accessors.append({"bufferView": view, "componentType": 5125, "count": len(group),
                          "type": "SCALAR", "min": [min(group)], "max": [max(group)]})
    doc = {"asset": {"version": "2.0", "generator": "DK64 Forge Phase 1C canonical DK"},
           "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"mesh": 0, "name": node_name}],
           "meshes": [{"name": "DK actor table 5 entry 3", "primitives": primitives}],
           "materials": materials, "images": images, "samplers": samplers, "textures": textures,
           "buffers": [{"uri": out.with_suffix(".bin").name, "byteLength": len(blob)}],
           "bufferViews": views, "accessors": accessors}
    out.with_suffix(".bin").write_bytes(blob)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    group_rows = []
    for group_index, (use, texgen) in enumerate(group_keys):
        if use is None:
            group_rows.append({
                "primitive": group_index, "triangle_count": len(grouped_triangles[(use, texgen)]),
                "untextured": "G_SETCOMBINE (0 - 0) * 0 + SHADE", "texture_generation": "none"})
            continue
        group_rows.append({
            "primitive": group_index, "triangle_count": len(grouped_triangles[(use, texgen)]),
            "source_address": f"0x{use.image_address:08x}",
            "dynamic_slot": use.dynamic_slot,
            "table_25_entry": use.texture_index,
            "format": "RGBA16", "dimensions": [use.width, use.height],
            "texture_scale": [use.scale_s, use.scale_t],
            "tile_wrap_mode": [use.wrap_s, use.wrap_t],
            "tile_shift": [use.shift_s, use.shift_t], "tile": use.tile,
            "tile_upper_left_quarter_texels": [use.tile_uls, use.tile_ult],
            "texture_filter": {G_TF_POINT: "G_TF_POINT", G_TF_BILERP: "G_TF_BILERP",
                               G_TF_AVERAGE: "G_TF_AVERAGE"}.get(use.texture_filter, "unknown"),
            "uv_texel_center_offset": 0.5 if use.texture_filter in (G_TF_BILERP, G_TF_AVERAGE) else 0.0,
            "loadblock_dxt0_odd_lines_swapped": use.odd_lines_swapped,
            "lod_level": use.lod_level,
            "texture_generation": "spherical" if texgen[0] and not texgen[1] else (
                "linear" if texgen[0] else "vertex_tc"),
        })
    return {"gltf": doc, "texture_entries": texture_metadata,
            "primitive_triangle_counts": {str(i): len(grouped_triangles[key])
                                          for i, key in enumerate(group_keys)},
            "triangle_groups": group_rows,
            "source_vertices": mesh.stats["referenced_source_vertices"],
            "actor_vertex_block_count": mesh.stats["original_vertex_count"],
            "export_vertices": len(positions), "triangles": sum(len(group) for group in indices) // 3,
            "dynamic_frame_policy": "first frame from each actor dynamic-texture record"}


def validate_gltf(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    need(doc["asset"]["version"] == "2.0", "not glTF 2.0")
    buffer = path.with_name(doc["buffers"][0]["uri"]).read_bytes()
    need(len(buffer) == doc["buffers"][0]["byteLength"], "buffer length mismatch")
    views = doc["bufferViews"]
    for view in views:
        need(view["buffer"] == 0 and 0 <= view["byteOffset"] <= len(buffer) and
             view["byteOffset"] + view["byteLength"] <= len(buffer), "bufferView out of bounds")
    pos_acc = doc["accessors"][0]
    attr_acc = doc["accessors"][1]
    need(len(doc["accessors"]) >= 3, "glTF accessors missing")
    need(pos_acc["type"] == "VEC3" and pos_acc["componentType"] == 5126, "bad position accessor")
    need(attr_acc["type"] in ("VEC3", "VEC4") and attr_acc["componentType"] == 5126,
         "bad vertex attribute accessor")
    need(pos_acc["count"] > 0, "empty position accessor")
    need(pos_acc["count"] * 12 <= views[pos_acc["bufferView"]]["byteLength"], "positions exceed accessor")
    attr_width = 12 if attr_acc["type"] == "VEC3" else 16
    need(attr_acc["count"] == pos_acc["count"] and
         attr_acc["count"] * attr_width <= views[attr_acc["bufferView"]]["byteLength"],
         "vertex attribute count/length mismatch")
    total_triangles = 0
    all_indices = []
    for primitive in doc["meshes"][0]["primitives"]:
        index_accessor = doc["accessors"][primitive["indices"]]
        need(index_accessor["type"] == "SCALAR" and index_accessor["componentType"] == 5125 and
             index_accessor["count"] > 0 and index_accessor["count"] % 3 == 0,
             "primitive index accessor is not a triangle list")
        need(index_accessor["count"] * 4 <= views[index_accessor["bufferView"]]["byteLength"],
             "indices exceed accessor")
        total_triangles += index_accessor["count"] // 3
        index_view = views[index_accessor["bufferView"]]
        # Each primitive is checked against its own POSITION accessor (a combined
        # skin + texture file keeps the bind skin's positions at accessor 0).
        primitive_positions = doc["accessors"][primitive["attributes"]["POSITION"]]["count"]
        primitive_indices = [i[0] for i in struct.iter_unpack(
            "<I", buffer[index_view["byteOffset"]:
                           index_view["byteOffset"] + index_accessor["count"] * 4])]
        need(all(0 <= i < primitive_positions for i in primitive_indices), "index out of bounds")
        all_indices.extend(primitive_indices)
        if "TEXCOORD_0" in primitive["attributes"]:
            uv = doc["accessors"][primitive["attributes"]["TEXCOORD_0"]]
            need(uv["type"] == "VEC2" and uv["componentType"] == 5126 and
                 uv["count"] == primitive_positions and
                 uv["count"] * 8 <= views[uv["bufferView"]]["byteLength"],
                 "bad TEXCOORD_0 accessor")
    for image in doc.get("images", []):
        need(path.parent.joinpath(image["uri"]).is_file(), f"missing image {image['uri']}")
    positions = [p for p in struct.iter_unpack("<fff", buffer[views[0]["byteOffset"]:
                                                     views[0]["byteOffset"] + pos_acc["count"] * 12])]
    attributes = [v for row in struct.iter_unpack("<fff" if attr_width == 12 else "<ffff",
                      buffer[views[attr_acc["bufferView"]]["byteOffset"]:
                             views[attr_acc["bufferView"]]["byteOffset"] + attr_acc["count"] * attr_width])
                  for v in row]
    need(all(math.isfinite(x) for p in positions for x in p), "nonfinite positions")
    need(all(math.isfinite(x) for x in attributes), "nonfinite vertex attribute")
    if attr_acc["type"] == "VEC3":
        lengths = [math.sqrt(sum(c * c for c in v)) for v in struct.iter_unpack(
            "<fff", buffer[views[attr_acc["bufferView"]]["byteOffset"]:
                           views[attr_acc["bufferView"]]["byteOffset"] + attr_acc["count"] * 12])]
        need(all(length == 0.0 or abs(length - 1.0) < 1e-5 for length in lengths),
             "normal vectors must be unit length or preserve a zero source normal")
    primitive_attributes = doc["meshes"][0]["primitives"][0]["attributes"]
    attribute_name = next(key for key in primitive_attributes if key != "POSITION")
    return {"valid": True, "vertices": len(positions), "triangles": total_triangles,
            "primitives": sum(len(m["primitives"]) for m in doc["meshes"]),
            "vertex_attribute": attribute_name,
            "texcoord_0": any("TEXCOORD_0" in p["attributes"]
                              for m in doc["meshes"] for p in m["primitives"]),
            "images_found": len(doc.get("images", [])),
            "bounding_box": [pos_acc["min"], pos_acc["max"]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path, nargs="?", help="user-supplied DK64 US ROM")
    parser.add_argument("--actor-asset", type=Path,
                        help="reuse an already extracted table-5 entry-3 actor asset without reopening the ROM")
    parser.add_argument("--textured", action="store_true",
                        help="export the canonical actor's observed texture state as a textured glTF")
    parser.add_argument("--texture-rom", type=Path,
                        help="verified local ROM for texture extraction when using --actor-asset")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--hand-state", default="0",
                        help="'idle' maps to source-backed mask 1; numeric 16-bit masks remain supported")
    args = parser.parse_args()
    try:
        need(bool(args.rom) != bool(args.actor_asset),
             "provide exactly one of ROM or --actor-asset")
        canonical_idle = args.hand_state.casefold() == "idle"
        hand_state = parse_hand_state(args.hand_state)
        if args.actor_asset:
            data = args.actor_asset.read_bytes()
            identity = {"source": "previously extracted actor asset; ROM was not revalidated"}
            extraction = {"source_path": str(args.actor_asset.resolve()),
                         "source_note": "reused Phase 1 table-5 entry-3 extraction"}
        else:
            rom, identity = identify_rom(args.rom)
            data, extraction = extract_entry(rom)
        actor = parse_actor(data)
        mesh = decode_actor_mesh(actor, hand_state)
        default_out = LOCAL_OUTPUT / "canonical_idle" if canonical_idle else LOCAL_OUTPUT
        out = (args.out or default_out).resolve()
        need(out.is_relative_to(LOCAL_OUTPUT), "derived output must stay under the ignored local_output directory")
        out.mkdir(parents=True, exist_ok=True)
        (out / "dk_actor_table5_entry3.bin").write_bytes(data)
        stem = "dk_static_idle" if canonical_idle else "dk_static"
        node_name = "Donkey Kong (canonical ordinary idle)" if canonical_idle else "Donkey Kong (static actor pose)"
        gltf_path = out / ("dk_static_idle_textured.gltf" if args.textured else f"{stem}.gltf")
        if args.textured:
            need(canonical_idle, "textured export is scoped to --hand-state idle")
            if args.rom:
                texture_rom = rom
            else:
                need(args.texture_rom is not None, "--textured with --actor-asset requires --texture-rom")
                texture_rom, _ = normalize_rom(args.texture_rom.read_bytes())
            texture_report = export_textured_gltf(mesh, actor, texture_rom, gltf_path)
        else:
            texture_report = None
            export_gltf(mesh, gltf_path, node_name)
        validation = validate_gltf(gltf_path)
        report = {"rom": identity, "extraction": extraction, "actor": actor.header,
                  "hand_state_label": "canonical_idle" if canonical_idle else f"mask_{hand_state}",
                  "mesh": mesh.stats, "texture_export": texture_report,
                  "gltf_validation": validation}
        report_name = "report_idle.json" if canonical_idle else "report.json"
        (out / report_name).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    except (ModelError, argparse.ArgumentTypeError, OSError, ValueError, IndexError, struct.error) as exc:
        print(f"Phase 1 stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
