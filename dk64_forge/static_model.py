"""Static DK64 models (props, maps, any actor) as viewport render data.

Glue between dk64_forge.core.mesh_decoder (display lists -> textured triangles) and the
JFG-style PreparedRenderData contract. Used by the Models and Levels tabs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .core import mesh_decoder, rom_model, texture_bank
from .render_data import PreparedBatch, PreparedRenderData, PreparedTexture

WRAP_NAMES = {0: "REPEAT", 1: "MIRROR", 2: "CLAMP", 3: "CLAMP"}
UNTEXTURED_RGBA = (0.78, 0.78, 0.78, 1.0)
MISSING_TEXTURE_RGBA = (0.85, 0.3, 0.75, 1.0)  # texture referenced but not decodable


@dataclass(frozen=True)
class StaticModel:
    render: PreparedRenderData
    triangles: int
    textured_triangles: int
    textures: int
    missing_textures: int
    unsupported: dict


class TextureCache:
    """Decodes each (image, usage) once per ROM; shared across models."""

    def __init__(self, rom: bytes) -> None:
        self.rom = rom
        self._pixels: dict = {}

    def pixels(self, texture: mesh_decoder.DrawTexture) -> bytes | None:
        key = (texture.table, texture.image, texture.usage.fmt, texture.usage.size, texture.usage.width,
               texture.usage.height, texture.usage.interleaved, texture.usage.palette)
        if key not in self._pixels:
            raw = texture_bank.table_entry(self.rom, texture.table, texture.image)
            palette = None
            if texture.usage.palette is not None:
                palette = texture_bank.table_entry(self.rom, 25, texture.usage.palette)
            self._pixels[key] = texture_bank.decode(raw, texture.usage, palette) if raw else None
        return self._pixels[key]


def _alpha_mode(rgba: bytes | None, corner_alpha: float) -> str:
    if corner_alpha < 0.999:
        return "BLEND"
    if rgba is None:
        return "OPAQUE"
    alphas = set(rgba[3::4])
    if alphas <= {0, 255}:
        return "MASK" if 0 in alphas else "OPAQUE"
    return "BLEND"


def render_data(mesh: mesh_decoder.StaticMesh, cache: TextureCache) -> StaticModel:
    """Group triangles by texture and culling into batches; decode each texture once."""
    texture_ids: dict = {}
    textures: list[PreparedTexture] = []
    missing = set()
    keyed: dict = {}
    for triangle, (texture, culled) in enumerate(zip(mesh.textures, mesh.culled)):
        texture_index = None
        if texture is not None:
            if texture not in texture_ids:
                rgba = cache.pixels(texture)
                if rgba is None:
                    texture_ids[texture] = None
                    missing.add(texture.image)
                else:
                    texture_ids[texture] = len(textures)
                    textures.append(PreparedTexture(
                        len(textures), texture.usage.width, texture.usage.height, rgba,
                        WRAP_NAMES[texture.wrap_s & 3], WRAP_NAMES[texture.wrap_t & 3]))
            texture_index = texture_ids[texture]
        alpha = min(mesh.colors[3 * triangle + k][3] for k in range(3))
        rgba = textures[texture_index].rgba if texture_index is not None else None
        mode = _alpha_mode(rgba, alpha)
        fallback = (MISSING_TEXTURE_RGBA if texture is not None and texture_index is None
                    else UNTEXTURED_RGBA)
        keyed.setdefault((texture_index, not culled, mode, fallback), []).append(triangle)

    positions, uvs, colors, batches = [], [], [], []
    for (texture_index, double_sided, mode, fallback), triangles in sorted(
            keyed.items(), key=lambda item: (item[0][2] != "OPAQUE", str(item[0]))):
        first = len(positions)
        for triangle in triangles:
            corners = range(3 * triangle, 3 * triangle + 3)
            positions.extend(mesh.positions[c] for c in corners)
            uvs.extend(mesh.uvs[c] for c in corners)
            colors.extend(mesh.colors[c] for c in corners)
        batches.append(PreparedBatch(
            first, len(positions) - first, texture_index, double_sided, texture_index is not None,
            fallback, None if texture_index is not None else "untextured", mode,
            depth_write=mode != "BLEND"))
    if not positions:
        positions = [(0.0, 0.0, 0.0)] * 3
        uvs = [(0.0, 0.0)] * 3
        colors = [(0.0, 0.0, 0.0, 0.0)] * 3
    minimum = tuple(min(p[axis] for p in positions) for axis in range(3))
    maximum = tuple(max(p[axis] for p in positions) for axis in range(3))
    render = PreparedRenderData(tuple(positions), tuple(uvs), tuple(batches), tuple(textures),
                                minimum, maximum, tuple(colors))
    textured = sum(1 for t in mesh.textures if t is not None and texture_ids.get(t) is not None)
    return StaticModel(render, mesh.triangle_count, textured, len(textures), len(missing),
                       mesh.stats.get("unsupported", {}))


def prop_model(rom: bytes, entry: int, cache: TextureCache) -> StaticModel | None:
    data = texture_bank.table_entry(rom, 4, entry)
    if not data or len(data) < 0x50:
        return None
    return render_data(mesh_decoder.decode(data, mesh_decoder.prop_ranges(data), rom=rom), cache)


def map_model(rom: bytes, entry: int, cache: TextureCache) -> StaticModel | None:
    data = texture_bank.table_entry(rom, 1, entry)
    if not data or len(data) < 0x140 or data[2:4] == b"\x08\x00":
        return None
    return render_data(mesh_decoder.decode(data, mesh_decoder.map_ranges(data), rom=rom), cache)


def actor_conditional_mask(entry: int) -> int:
    """Playable Kongs use their verified hand mask; other actors the spawn default -1."""
    from .characters import CHARACTERS
    for spec in CHARACTERS.values():
        if spec.table5_entry == entry:
            return spec.hand_mask
    return -1


def actor_model(rom: bytes, entry: int, cache: TextureCache) -> StaticModel | None:
    data = texture_bank.table_entry(rom, 5, entry)
    if not data:
        return None
    try:
        actor = rom_model.parse_actor(data)
    except Exception:  # some entries are not regular actor models
        return None
    try:
        dynamic = rom_model.parse_dynamic_textures(actor)  # eyes/mouths: first frame
    except Exception:
        dynamic = None
    mesh = mesh_decoder.decode(actor.data, mesh_decoder.actor_ranges(actor), rom=rom,
                               bone_offsets=mesh_decoder.actor_bone_offsets(actor), dynamic=dynamic,
                               conditional_mask=actor_conditional_mask(entry))
    return render_data(mesh, cache)


def marker_skeleton(render: PreparedRenderData):
    """A single joint at the model centre; static models have no bones to show."""
    from .debug_view import PreparedSkeletonDebug
    centre = tuple((a + b) / 2 for a, b in zip(render.bounds_minimum, render.bounds_maximum))
    return PreparedSkeletonDebug((0,), (centre,), ())


def export_glb(model: StaticModel, path, name: str) -> dict:
    """Write a self-contained binary glTF: one primitive per batch, COLOR_0 for the shade,
    embedded PNG textures, alpha mode and double-sidedness from the batch."""
    import json
    import struct as _struct
    render = model.render
    binary = bytearray()
    views, accessors = [], []

    def add_view(blob: bytes, target: int | None = None) -> int:
        while len(binary) % 4:
            binary.append(0)
        view = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(blob)}
        if target:
            view["target"] = target
        binary.extend(blob)
        views.append(view)
        return len(views) - 1

    def add_accessor(values, kind: str, components: int, bounds: bool = False) -> int:
        flat = [c for value in values for c in value]
        accessor = {"bufferView": add_view(_struct.pack(f"<{len(flat)}f", *flat), 34962),
                    "componentType": 5126, "count": len(values), "type": kind}
        if bounds:
            accessor["min"] = [min(v[i] for v in values) for i in range(components)]
            accessor["max"] = [max(v[i] for v in values) for i in range(components)]
        accessors.append(accessor)
        return len(accessors) - 1

    images, textures, materials, primitives = [], [], [], []
    image_of: dict[int, int] = {}
    wrap = {"REPEAT": 10497, "MIRROR": 33648, "CLAMP": 33071}
    samplers = []
    for texture in render.textures:
        png = texture_bank.rgba_png(texture.width, texture.height, texture.rgba)
        images.append({"bufferView": add_view(png), "mimeType": "image/png"})
        samplers.append({"magFilter": 9729, "minFilter": 9729,
                         "wrapS": wrap[texture.wrap_s], "wrapT": wrap[texture.wrap_t]})
        textures.append({"source": len(images) - 1, "sampler": len(samplers) - 1})
        image_of[texture.texture_index] = len(textures) - 1
    for batch in render.batches:
        corners = range(batch.first_vertex, batch.first_vertex + batch.vertex_count)
        attributes = {
            "POSITION": add_accessor([render.positions[i] for i in corners], "VEC3", 3, True),
            "COLOR_0": add_accessor([render.colors[i] for i in corners], "VEC4", 4),
        }
        material = {"name": f"material_{len(materials)}", "doubleSided": batch.double_sided,
                    "alphaMode": batch.alpha_mode,
                    "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}}
        if batch.alpha_mode == "MASK":
            material["alphaCutoff"] = 0.5
        if batch.texture_index is not None:
            attributes["TEXCOORD_0"] = add_accessor([render.uvs[i] for i in corners], "VEC2", 2)
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": image_of[batch.texture_index]}
        else:
            material["pbrMetallicRoughness"]["baseColorFactor"] = list(batch.fallback_rgba)
        materials.append(material)
        primitives.append({"attributes": attributes, "material": len(materials) - 1, "mode": 4})
    document = {
        "asset": {"version": "2.0", "generator": "DK64 Forge static model export"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0}],
        "meshes": [{"name": name, "primitives": primitives}],
        "materials": materials, "accessors": accessors, "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    if textures:
        document.update(images=images, textures=textures, samplers=samplers)
    while len(binary) % 4:
        binary.append(0)
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    blob = (_struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(binary))
            + _struct.pack("<II", len(text), 0x4E4F534A) + text
            + _struct.pack("<II", len(binary), 0x004E4942) + bytes(binary))
    from pathlib import Path
    Path(path).write_bytes(blob)
    return {"primitives": len(primitives), "textures": len(textures),
            "triangles": sum(b.face_count for b in render.batches)}
