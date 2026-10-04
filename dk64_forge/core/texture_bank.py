"""DK64 texture bank: find, describe and decode the ROM's geometry textures.

The ROM stores raw texels without any header (no format, size or name), so every
property comes from how a display list loads the texture:

- G_SETTIMG (0xFD) names the image: segment 0 + index into pointer table 25
  (TABLE_25_TEXTURES_GEOMETRY), as verified for the Kong actors (Phase 1C) and
  as dk64_lib resolves map textures.
- G_SETTILE (0xF5) gives format/size of the render tile, G_SETTILESIZE (0xF2) its
  width/height; G_LOADBLOCK (0xF3) the texel count and dxt; G_LOADTLUT (0xF0)
  marks a palette image for CI formats.
- G_LOADBLOCK with dxt=0 means the image is stored pre-interleaved (odd TMEM lines
  word-swapped), as established for DK in Phase 1F.

Sources scanned (layout facts: Phase 1 for actors; DK64 Randomizer model_port.py
for model-two props: DL at header 0x40..0x44/0x48; dk64_lib facts for maps: DL at
header 0x34..0x38). The scan is linear over each display-list byte range, so a
usage is "the state when a texture was loaded", not an execution trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import zlib

import numpy as np

from . import rom_model

TABLE_ACTORS, TABLE_PROPS, TABLE_MAPS, TABLE_TEXTURES = 5, 4, 1, 25
FORMATS = {0: "RGBA", 1: "YUV", 2: "CI", 3: "IA", 4: "I"}
SIZES = {0: 4, 1: 8, 2: 16, 3: 32}  # bits per texel


@dataclass(frozen=True)
class TextureUsage:
    """One display-list load of a table-25 texture."""
    fmt: int
    size: int            # G_IM_SIZ_* (0=4b, 1=8b, 2=16b, 3=32b)
    width: int
    height: int
    interleaved: bool    # G_LOADBLOCK dxt == 0
    palette: int | None  # table-25 index of the TLUT image for CI formats
    user: str            # e.g. "actor 3", "prop 1", "map 7"

    @property
    def format_name(self) -> str:
        return f"{FORMATS.get(self.fmt, f'fmt{self.fmt}')}{SIZES.get(self.size, '?')}"


def entry_count(rom: bytes, table: int) -> int:
    return rom_model.be32(rom, rom_model.POINTER_BASE + 0x80 + table * 4)


def table_entry(rom: bytes, table: int, index: int) -> bytes | None:
    """Decompressed entry, or None for empty/invalid entries."""
    try:
        data, _info = rom_model.extract_entry(rom, table, index)
    except (rom_model.ModelError, zlib.error, ValueError):
        return None
    return data or None


def scan_display_list(data: bytes, start: int, end: int, user: str) -> list[tuple[int, TextureUsage]]:
    """Linear F3DEX2 state scan; returns (table-25 index, usage) per texture load."""
    found: list[tuple[int, TextureUsage]] = []
    image = None            # (segment address word, fmt, size)
    tiles: dict[int, dict] = {}
    sizes: dict[int, tuple[int, int]] = {}
    pending = None          # (index, dxt0) of the last G_LOADBLOCK awaiting its render tile
    palette = None
    start = max(0, start - start % 8)
    end = min(len(data), end)
    for pc in range(start, end - 7, 8):
        op = data[pc]
        w0 = int.from_bytes(data[pc:pc + 4], "big")
        w1 = int.from_bytes(data[pc + 4:pc + 8], "big")
        if op == 0xFD:  # G_SETTIMG
            image = (w1, (w0 >> 21) & 7, (w0 >> 19) & 3)
        elif op == 0xF5:  # G_SETTILE
            tiles[(w1 >> 24) & 7] = {"fmt": (w0 >> 21) & 7, "size": (w0 >> 19) & 3,
                                    "mask_t": (w1 >> 14) & 15, "mask_s": (w1 >> 4) & 15}
        elif op == 0xF0 and image is not None and image[0] >> 24 == 0:  # G_LOADTLUT
            palette = image[0] & 0xFFFFFF
        elif op == 0xF3 and image is not None and image[0] >> 24 == 0:  # G_LOADBLOCK
            pending = (image[0] & 0xFFFFFF, (w1 & 0xFFF) == 0, ((w1 >> 12) & 0xFFF) + 1)
        elif op == 0xF4 and image is not None and image[0] >> 24 == 0:  # G_LOADTILE (linear in RAM)
            pending = (image[0] & 0xFFFFFF, False, None)
        elif op == 0xF2:  # G_SETTILESIZE: the render tile size completes a usage
            tile = (w1 >> 24) & 7
            uls, ult = (w0 >> 12) & 0xFFF, w0 & 0xFFF
            lrs, lrt = (w1 >> 12) & 0xFFF, w1 & 0xFFF
            sizes[tile] = ((lrs - uls) // 4 + 1, (lrt - ult) // 4 + 1)
            descriptor = tiles.get(tile)
            if pending is not None and descriptor is not None and tile == 0:
                index, interleaved, _texels = pending
                width, height = sizes[tile]
                found.append((index, TextureUsage(descriptor["fmt"], descriptor["size"], width, height,
                                                  interleaved,
                                                  palette if descriptor["fmt"] == 2 else None, user)))
                pending = None
        elif op in (0x05, 0x06, 0x07) and pending is not None:
            # Triangles before any G_SETTILESIZE: fall back to the tile masks and texel count.
            descriptor = tiles.get(0)
            index, interleaved, texels = pending
            if descriptor and descriptor["mask_s"] and descriptor["mask_t"]:
                width, height = 1 << descriptor["mask_s"], 1 << descriptor["mask_t"]
                found.append((index, TextureUsage(descriptor["fmt"], descriptor["size"], width, height,
                                                  interleaved,
                                                  palette if descriptor["fmt"] == 2 else None, user)))
            pending = None
    return found


def _segment_usages(data: bytes, start: int, end: int, user: str) -> dict[int, TextureUsage]:
    """Usages of segmented images (G_SETTIMG segment 0x0C-0x0E), keyed by segment."""
    patched = bytearray(data)
    found: dict[int, TextureUsage] = {}
    for pc in range(start - start % 8, min(len(data), end) - 7, 8):
        if data[pc] == 0xFD and 0 < data[pc + 4] < 16:
            segment = data[pc + 4]
            # Re-point the image to a marker index so the normal scan records its usage.
            patched[pc + 4:pc + 8] = (0x00FF0000 | segment).to_bytes(4, "big")
    for index, usage in scan_display_list(bytes(patched), start, end, user):
        if index >> 16 == 0xFF and index & 0xFF not in found:
            found[index & 0xFF] = usage
    return found


def _actor_ranges(rom: bytes, index: int):
    data = table_entry(rom, TABLE_ACTORS, index)
    if data is None:
        return None
    try:
        actor = rom_model.parse_actor(data)
    except Exception:
        return None
    return data, actor.dl_start, actor.dl_end


def _prop_ranges(rom: bytes, index: int):
    data = table_entry(rom, TABLE_PROPS, index)
    if data is None or len(data) < 0x50:
        return None
    start = int.from_bytes(data[0x40:0x44], "big")
    end = int.from_bytes(data[0x48:0x4C], "big")  # DL + overlay DL end at the vertex start
    if not 0x50 <= start < end <= len(data):
        return None
    return data, start, end


def _map_ranges(rom: bytes, index: int):
    data = table_entry(rom, TABLE_MAPS, index)
    if data is None or len(data) < 0x74 or data[2:4] == b"\x08\x00":  # pointer stub entry
        return None
    start = int.from_bytes(data[0x34:0x38], "big")
    end = int.from_bytes(data[0x38:0x3C], "big")
    if not 0 < start < end <= len(data):
        return None
    return data, start, end


def prop_name(rom: bytes, index: int) -> str | None:
    """Model-two props carry an ASCII name at header offset 0x0C (e.g. 'torches')."""
    data = table_entry(rom, TABLE_PROPS, index)
    if data is None or len(data) < 0x20:
        return None
    raw = data[0x0C:0x20].split(b"\x00", 1)[0]
    text = raw.decode("ascii", errors="ignore")
    return text if text and text.isprintable() else None


@dataclass
class TextureEntry:
    index: int
    byte_size: int | None
    usages: list[TextureUsage] = field(default_factory=list)

    @property
    def users(self) -> list[str]:
        return list(dict.fromkeys(u.user for u in self.usages))

    @property
    def primary(self) -> TextureUsage | None:
        """Most common (format, size, dims, interleave) combination among the usages."""
        if not self.usages:
            return None
        counts: dict[tuple, int] = {}
        for usage in self.usages:
            key = (usage.fmt, usage.size, usage.width, usage.height, usage.interleaved, usage.palette)
            counts[key] = counts.get(key, 0) + 1
        best = max(counts, key=counts.get)
        return next(u for u in self.usages
                    if (u.fmt, u.size, u.width, u.height, u.interleaved, u.palette) == best)


def scan_bank(rom: bytes, progress=None, *, table: int = 25) -> dict[int, TextureEntry]:
    """Every table-25 texture with all display-list usages found in actors, props and maps."""
    entries: dict[int, TextureEntry] = {}
    sources = ((TABLE_ACTORS, "actor", _actor_ranges), (TABLE_PROPS, "prop", _prop_ranges),
               (TABLE_MAPS, "map", _map_ranges))
    for source_table, kind, ranges in sources:
        for index in range(entry_count(rom, source_table)):
            found = ranges(rom, index)
            if found is None:
                continue
            data, start, end = found
            from . import texture_animation
            animations = (texture_animation.prop_animations(data) if kind == "prop" else
                          texture_animation.map_animations(data) if kind == "map" else ())
            by_key = {a.key: a for a in animations}
            for texture, usage in scan_display_list(data, start, end, f"{kind} {index}"):
                animation = by_key.get(texture) if kind == "prop" else None
                target = 7 if animation else 25
                if target == table:
                    for frame in animation.frames if animation else (texture,):
                        entries.setdefault(frame, TextureEntry(frame, None)).usages.append(usage)
            if kind == "map" and table == 7:
                usages = _segment_usages(data, start, end, f"map {index}")
                for animation in animations:
                    if animation.key in usages:
                        for frame in animation.frames:
                            entries.setdefault(frame, TextureEntry(frame, None)).usages.append(usages[animation.key])
            if progress:
                progress(kind, index)
    # Actor dynamic texture slots (segments 0x0C-0x0E, e.g. eyes/mouths): every frame of a
    # slot is loaded exactly like the slot's own usage (rom_model.parse_dynamic_textures).
    for index in range(entry_count(rom, TABLE_ACTORS) if table == 25 else 0):
        found = _actor_ranges(rom, index)
        if found is None:
            continue
        data, start, end = found
        try:
            slots = rom_model.parse_dynamic_textures(rom_model.parse_actor(data))
        except Exception:
            continue
        for slot, usage in _segment_usages(data, start, end, f"actor {index}").items():
            for frame in slots.get(slot, ()):
                entries.setdefault(frame, TextureEntry(frame, None)).usages.append(usage)
    if table == 14:
        from .hud_textures import usages
        for index, layouts in usages(rom).items():
            entries.setdefault(index, TextureEntry(index, None)).usages.extend(layouts)
    for index in range(entry_count(rom, table)):
        raw = table_entry(rom, table, index)
        if raw is not None:
            entries.setdefault(index, TextureEntry(index, None)).byte_size = len(raw)
    return entries


# --- decoding ------------------------------------------------------------------------------

def deinterleave(raw: bytes, row_bytes: int, height: int) -> bytes:
    """Undo the dxt=0 pre-interleave: odd rows have the 32-bit halves of each 64-bit word swapped.

    For 16-bit texels this is x ^ 2 (Phase 1F); the same byte rule covers 4/8-bit texels.
    """
    if row_bytes % 8:
        return raw
    rows = min(height, len(raw) // row_bytes)
    out = np.frombuffer(raw, dtype=np.uint8).copy()
    words = out[:rows * row_bytes].reshape(rows, row_bytes // 8, 2, 4)
    words[1::2] = words[1::2, :, ::-1, :]
    return out.tobytes()


def _rgba5551(value: int) -> tuple[int, int, int, int]:
    r, g, b = (value >> 11) & 31, (value >> 6) & 31, (value >> 1) & 31
    return (r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2), 255 if value & 1 else 0


def _rgba5551_array(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.uint32)
    out = np.empty((len(values), 4), dtype=np.uint8)
    for channel, shift in enumerate((11, 6, 1)):
        c = (values >> shift) & 31
        out[:, channel] = (c << 3) | (c >> 2)
    out[:, 3] = np.where(values & 1, 255, 0)
    return out


def _texels(data: bytes, bits: int, count: int) -> np.ndarray:
    raw = np.frombuffer(data, dtype=np.uint8)
    if bits == 4:
        texels = np.empty(count, dtype=np.uint32)
        texels[0::2] = raw[:(count + 1) // 2] >> 4
        texels[1::2] = raw[:count // 2] & 15
        return texels
    if bits == 8:
        return raw[:count].astype(np.uint32)
    if bits == 16:
        return np.frombuffer(data, dtype=">u2", count=count).astype(np.uint32)
    return np.frombuffer(data, dtype=">u4", count=count).astype(np.uint32)


def decode(raw: bytes, usage: TextureUsage, palette: bytes | None = None) -> bytes | None:
    """RGBA8888 pixels (width*height*4) for one usage, or None when it cannot be decoded.

    Formats: RGBA16/32, IA4/8/16, I4/8, CI4/8 (RGBA16 TLUT). I/IA previews show the
    intensity as grey with the stored alpha (I: opaque). RGBA32 interleaving is not undone
    (TMEM splits 32-bit texels; evidence mixed, as in JFG Forge). Vectorised with NumPy;
    results are identical to the per-texel definition above each branch.
    """
    bits = SIZES.get(usage.size)
    if bits is None or usage.width <= 0 or usage.height <= 0:
        return None
    row_bytes = usage.width * bits // 8
    needed = row_bytes * usage.height
    if len(raw) < needed or usage.width * bits % 8:
        return None
    data = raw[:needed]
    if usage.interleaved and bits != 32:
        data = deinterleave(data, row_bytes, usage.height)
    count = usage.width * usage.height
    fmt = usage.fmt
    value = _texels(data, bits, count)
    out = np.empty((count, 4), dtype=np.uint8)
    if fmt == 2:  # CI
        entries = 16 if bits == 4 else 256
        # A CI image needs a TLUT with all its colours; a 16-colour TLUT paired with a CI8
        # usage (seen on some maps) is not trusted, so such images stay "not decoded".
        if palette is None or len(palette) < 2 * entries or bits not in (4, 8):
            return None
        colours = _rgba5551_array(np.frombuffer(palette, dtype=">u2", count=entries))
        out[:] = colours[value]
    elif fmt == 0 and bits == 16:
        out[:] = _rgba5551_array(value)
    elif fmt == 0 and bits == 32:
        for channel, shift in enumerate((24, 16, 8, 0)):
            out[:, channel] = (value >> shift) & 255
    elif fmt == 3 and bits == 16:
        out[:, 0:3] = (value >> 8)[:, None]
        out[:, 3] = value & 255
    elif fmt == 3 and bits == 8:
        out[:, 0:3] = ((value >> 4) * 17)[:, None]
        out[:, 3] = (value & 15) * 17
    elif fmt == 3 and bits == 4:
        out[:, 0:3] = ((value >> 1) * 255 // 7)[:, None]
        out[:, 3] = np.where(value & 1, 255, 0)
    elif fmt == 4 and bits == 8:
        out[:, 0:3] = value[:, None]
        out[:, 3] = 255
    elif fmt == 4 and bits == 4:
        out[:, 0:3] = (value * 17)[:, None]
        out[:, 3] = 255
    else:
        return None
    return out.tobytes()


def rgba_png(width: int, height: int, rgba: bytes) -> bytes:
    """Minimal PNG writer (RGBA8888)."""
    import binascii
    import struct

    rows = b"".join(b"\x00" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = binascii.crc32(payload, binascii.crc32(kind)) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


# --- names ---------------------------------------------------------------------------------

def decode_mip_levels(raw: bytes, usage: TextureUsage):
    """Decode complete contiguous mip levels after a raw base image.

    Used for geometry RGBA16 textures; remaining short padding is ignored.
    Other formats need their own mip/TMEM layout evidence.
    """
    if (usage.fmt, usage.size) != (0, 2):
        return ()
    width, height = usage.width, usage.height
    at = width * height * 2
    levels = []
    while width > 1 or height > 1:
        width, height = max(1, width // 2), max(1, height // 2)
        size = width * height * 2
        if at + size > len(raw):
            break
        mip = TextureUsage(0, 2, width, height, usage.interleaved, None, usage.user)
        rgba = decode(raw[at:at + size], mip)
        if rgba is None:
            break
        levels.append((width, height, rgba))
        at += size
    return tuple(levels)

def user_display_name(user: str, prop_names: dict[int, str | None]) -> str:
    """'actor 3' -> 'DK', 'prop 1' -> 'torches', 'map 7' -> 'Japes'."""
    from .names import ACTOR_MODEL_NAMES, MAP_NAMES
    if user.startswith("HUD "):
        return user
    kind, number = user.split()
    index = int(number)
    if kind == "actor":
        return ACTOR_MODEL_NAMES[index] if index < len(ACTOR_MODEL_NAMES) else f"Actor {index}"
    if kind == "prop":
        return prop_names.get(index) or f"Prop {index}"
    return MAP_NAMES.get(index, f"Map {index}")


def derived_name(entry: TextureEntry, prop_names: dict[int, str | None], palette_of: set[int]) -> str:
    """Usage-derived label (the ROM has no texture names), e.g. 'Japes +12 · 32×32'."""
    users = entry.users
    if users:
        first = user_display_name(users[0], prop_names)
        extra = f" +{len(users) - 1}" if len(users) > 1 else ""
        usage = entry.primary
        return f"{first}{extra} · {usage.width}×{usage.height}"
    if entry.index in palette_of:
        return "Palette (TLUT)"
    return "Unused"


# --- bank view model (used by the Textures tab) --------------------------------------------

@dataclass
class BankItem:
    """One table-25 texture as listed in the Textures tab."""
    index: int
    byte_size: int
    name: str
    usage: TextureUsage | None       # primary usage (format/size) or None if unreferenced
    users: tuple[str, ...]           # display names of every model/map using it
    is_palette: bool
    conflicting: bool                # usages disagree on format/size
    table: int = 25
    references: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        if self.usage is not None:
            return "used"
        return "palette" if self.is_palette else "unused"

    def guess_usage(self) -> TextureUsage | None:
        """GUESS for unreferenced data: RGBA16 rows of 32 texels (the most common DK64 layout)."""
        if self.byte_size < 64 or self.byte_size % 64:
            return None
        return TextureUsage(0, 2, 32, self.byte_size // 64, False, None, "guess")


def build_bank_items(rom: bytes, progress=None, *, table: int = 25) -> list[BankItem]:
    if table not in (7, 14, 25):
        raise ValueError("Unsupported texture bank")
    bank = scan_bank(rom, progress, table=table)
    palettes = {u.palette for entry in bank.values() for u in entry.usages if u.palette is not None}
    prop_names = {index: prop_name(rom, index) for index in range(entry_count(rom, TABLE_PROPS))}
    items = []
    for index in sorted(bank):
        entry = bank[index]
        if not entry.byte_size:
            continue
        users = tuple(dict.fromkeys(user_display_name(user, prop_names) for user in entry.users))
        shapes = {(u.fmt, u.size, u.width, u.height) for u in entry.usages}
        items.append(BankItem(index, entry.byte_size, derived_name(entry, prop_names, palettes),
                              entry.primary, users, index in palettes, len(shapes) > 1, table, tuple(entry.users)))
    return items


def decode_item(rom: bytes, item: BankItem, guess: bool = False) -> tuple[int, int, bytes] | None:
    """(width, height, RGBA8888) for an item's primary usage, its palette, or a GUESS layout."""
    raw = table_entry(rom, item.table, item.index)
    if raw is None:
        return None
    usage = item.usage
    if usage is None and item.is_palette:
        count = len(raw) // 2  # show the TLUT itself as a strip of RGBA16 colours
        usage = TextureUsage(0, 2, min(count, 16), max(1, count // 16), False, None, "palette")
    elif usage is None and guess:
        usage = item.guess_usage()
    if usage is None:
        return None
    palette = table_entry(rom, item.table, usage.palette) if usage.palette is not None else None
    rgba = decode(raw, usage, palette)
    return None if rgba is None else (usage.width, usage.height, rgba)
