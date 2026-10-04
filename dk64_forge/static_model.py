"""Static DK64 models (props, maps, any actor) as viewport render data.

Glue between dk64_forge.core.mesh_decoder (display lists -> textured triangles) and the
JFG-style PreparedRenderData contract. Used by the Models and Levels tabs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .core import mesh_decoder, rom_model, texture_bank, texture_animation, prop_animation
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
    rigid_joints: tuple[int, ...] = ()
    texture_sources: tuple = ()
    normals: tuple = ()


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
                palette = texture_bank.table_entry(self.rom, texture.table, texture.usage.palette)
            self._pixels[key] = texture_bank.decode(raw, texture.usage, palette) if raw else None
        return self._pixels[key]


_TEXTURE_ALPHA: dict = {}


def _texture_alpha_mode(rgba: bytes) -> str:
    """OPAQUE / MASK / BLEND from a texture's alpha values (cached per pixel buffer)."""
    key = id(rgba)
    cached = _TEXTURE_ALPHA.get(key)
    if cached is None or cached[0] is not rgba:
        alphas = set(np.unique(np.frombuffer(rgba, dtype=np.uint8)[3::4]).tolist())
        mode = ("MASK" if 0 in alphas else "OPAQUE") if alphas <= {0, 255} else "BLEND"
        if len(_TEXTURE_ALPHA) > 4096:
            _TEXTURE_ALPHA.clear()
        cached = _TEXTURE_ALPHA[key] = (rgba, mode)
    return cached[1]


def _alpha_mode(rgba: bytes | None, corner_alpha: float) -> str:
    if corner_alpha < 0.999:
        return "BLEND"
    if rgba is None:
        return "OPAQUE"
    return _texture_alpha_mode(rgba)


def render_data(mesh: mesh_decoder.StaticMesh, cache: TextureCache, *, blends=None) -> StaticModel:
    """Group triangles by texture and culling into batches; decode each texture once."""
    texture_ids: dict = {}
    textures: list[PreparedTexture] = []
    missing = set()
    keyed: dict = {}
    def texture_id(texture):
        if texture is None:
            return None
        if texture not in texture_ids:
            rgba = cache.pixels(texture)
            blend = (blends or {}).get((texture.table, texture.image))
            if rgba is not None and blend is not None:
                from dataclasses import replace
                image, fraction = blend
                other = cache.pixels(replace(texture, image=image))
                if other is not None and len(other) == len(rgba):
                    a = np.frombuffer(rgba, dtype=np.uint8).astype(float)
                    b = np.frombuffer(other, dtype=np.uint8)
                    rgba = np.rint(a * (1 - fraction) + b * fraction).astype(np.uint8).tobytes()
            if rgba is None:
                texture_ids[texture] = None
                missing.add(texture.image)
            else:
                texture_ids[texture] = len(textures)
                textures.append(PreparedTexture(len(textures), texture.usage.width, texture.usage.height, rgba,
                    WRAP_NAMES[texture.wrap_s & 3], WRAP_NAMES[texture.wrap_t & 3],
                    () if blend else texture_bank.decode_mip_levels(texture_bank.table_entry(cache.rom, texture.table, texture.image) or b"", texture.usage)))
        return texture_ids[texture]

    for triangle, (texture, culled) in enumerate(zip(mesh.textures, mesh.culled)):
        texture_index = texture_id(texture)
        texture1_index = texture_id(mesh.secondary_textures[triangle]) if mesh.secondary_textures else None
        alpha = min(mesh.colors[3 * triangle + k][3] for k in range(3))
        rgba = textures[texture_index].rgba if texture_index is not None else None
        material = mesh.materials[triangle] if mesh.materials else mesh_decoder.rdp.MaterialState()
        mode = _alpha_mode(rgba, mesh_decoder.rdp.opaque_alpha(material, alpha))
        fallback = (MISSING_TEXTURE_RGBA if texture is not None and texture_index is None
                    else UNTEXTURED_RGBA)
        keyed.setdefault((texture_index, not culled, mode, fallback, mesh.materials[triangle] if mesh.materials else mesh_decoder.rdp.MaterialState(), texture1_index), []).append(triangle)

    positions, uvs, colors, batches, joints, uvs1 = [], [], [], [], [], []
    normals = []
    for (texture_index, double_sided, mode, fallback, material, texture1_index), triangles in sorted(
            keyed.items(), key=lambda item: (item[0][2] != "OPAQUE", str(item[0]))):
        first = len(positions)
        for triangle in triangles:
            corners = range(3 * triangle, 3 * triangle + 3)
            joints.extend(mesh.joints[c] if mesh.joints else 0 for c in corners)
            normals.extend(mesh.normals[c] if mesh.normals else (0.,0.,0.) for c in corners)
            positions.extend(mesh.positions[c] for c in corners)
            uvs.extend(mesh.uvs[c] for c in corners)
            uvs1.extend(mesh.secondary_uvs[c] if mesh.secondary_uvs else mesh.uvs[c] for c in corners)
            colors.extend(mesh.colors[c] for c in corners)
        batches.append(PreparedBatch(
            first, len(positions) - first, texture_index, double_sided, texture_index is not None,
            fallback, None if texture_index is not None else "untextured", mode,
            depth_write=mode != "BLEND", material=material, texture1_index=texture1_index))
    if not positions:
        positions = [(0.0, 0.0, 0.0)] * 3
        uvs = [(0.0, 0.0)] * 3
        uvs1 = uvs.copy()
        colors = [(0.0, 0.0, 0.0, 0.0)] * 3
    minimum = tuple(min(p[axis] for p in positions) for axis in range(3))
    maximum = tuple(max(p[axis] for p in positions) for axis in range(3))
    render = PreparedRenderData(tuple(positions), tuple(uvs), tuple(batches), tuple(textures),
                                minimum, maximum, tuple(colors), tuple(uvs1))
    textured = sum(1 for t in mesh.textures if t is not None and texture_ids.get(t) is not None)
    return StaticModel(render, mesh.triangle_count, textured, len(textures), len(missing),
                       mesh.stats.get("unsupported", {}), tuple(joints),
                       tuple(source for source, index in texture_ids.items() if index is not None), tuple(normals))


def prop_model(rom: bytes, entry: int, cache: TextureCache, *, frame: int = 0, tick: int | None = None, track: int | None = None, speed: int = 1, texture_playback: bool = True) -> StaticModel | None:
    data = texture_bank.table_entry(rom, 4, entry)
    if not data or len(data) < 0x50:
        return None
    animations = texture_animation.prop_animations(data)
    texture_tick = tick if texture_playback else None
    overrides = {a.key: (a.table, a.image(frame if texture_tick is None else texture_tick // a.ticks_per_frame)) for a in animations}
    blends = {(a.table, a.image(tick // a.ticks_per_frame)): (a.image(tick // a.ticks_per_frame + 1), (tick % a.ticks_per_frame) / a.ticks_per_frame)
              for a in animations if texture_tick is not None and a.interpolate}
    if data[0x1C] == 2:
        from dataclasses import replace
        from .core import billboards
        model = render_data(billboards.decode(data, overrides), cache, blends=blends)
        return replace(model, render=replace(model.render,
                       batches=tuple(replace(batch, billboard_center=(0., 0., 0.)) for batch in model.render.batches)))
    rig = prop_animation.parse(data)
    matrices = rig.pose(track, tick or 0, speed) if rig else None
    return render_data(mesh_decoder.decode(data, mesh_decoder.prop_ranges(data), rom=rom,
                                         image_overrides=overrides, matrices=matrices), cache, blends=blends)


MAP_SCALE = 1.0 / 3.0  # world units per map vertex unit (guScale in the map loader)


def map_model(rom: bytes, entry: int, cache: TextureCache, *, frame: int = 0, tick: int | None = None, chunks=None) -> StaticModel | None:
    data = texture_bank.table_entry(rom, 1, entry)
    if not data or len(data) < 0x140 or data[2:4] == b"\x08\x00":
        return None
    animations = texture_animation.map_animations(data)
    groups = {}
    for animation in animations:
        sample = frame if tick is None else tick // animation.ticks_per_frame
        groups.setdefault(animation.group, {})[animation.key] = (animation.image(sample),)
    ranges = mesh_decoder.map_ranges(data, with_chunk=True)
    if chunks is not None:
        ranges = [row for row in ranges if row[3] in chunks]
    from .core.map_effects import append_geometry
    data, effect_ranges, unsupported_effects = append_geometry(data, entry, tick or 0, chunks)
    mesh = mesh_decoder.decode(data, ranges + effect_ranges,
                               rom=rom, dynamic_groups=groups, dynamic_table=7, image_overrides={0xFFFF04: (7, 0x3E0)})
    # The map loader func_global_asm_80650ECC draws map geometry through
    # guScale(mtx, 1/3, 1/3, 1/3) (constant at 0x80758C60, read from the ROM's code), so map
    # vertices are three times world units; setup objects and spawns use world units.
    mesh.positions = [(x * MAP_SCALE, y * MAP_SCALE, z * MAP_SCALE) for x, y, z in mesh.positions]
    for effect in unsupported_effects:
        mesh.stats["unsupported"][f"Procedural effect {effect}"] = 1
    return render_data(mesh, cache)


def actor_conditional_mask(entry: int) -> int:
    """Playable Kongs (and their instrument models) use the hand mask; others the spawn default -1."""
    from .characters import CHARACTERS, INSTRUMENT_ENTRIES
    for spec in CHARACTERS.values():
        if entry in (spec.table5_entry, INSTRUMENT_ENTRIES.get(spec.key)):
            return spec.hand_mask
    return -1


def actor_model(rom: bytes, entry: int, cache: TextureCache, *, frame: int = 0, tick: int | None = None) -> StaticModel | None:
    data = texture_bank.table_entry(rom, 5, entry)
    if not data:
        return None
    try:
        actor = rom_model.parse_actor(data)
    except Exception:  # some entries are not regular actor models
        return None
    try:
        dynamic = {slot: (frames[frame % len(frames)],) for slot, frames in rom_model.parse_dynamic_textures(actor).items() if frames}
    except Exception:
        dynamic = None
    # Generic actors carry baked RGBA; display lists explicitly enable lighting
    # where their vertex bytes instead represent normals (e.g. playable Kongs).
    mesh = mesh_decoder.decode(actor.data, mesh_decoder.actor_ranges(actor), rom=rom,
                               bone_offsets=mesh_decoder.actor_bone_offsets(actor), dynamic=dynamic, initial_geometry=0,
                               conditional_mask=actor_conditional_mask(entry))
    return render_data(mesh, cache)


def marker_skeleton(render: PreparedRenderData):
    """A single joint at the model centre; static models have no bones to show."""
    from .debug_view import PreparedSkeletonDebug
    centre = tuple((a + b) / 2 for a, b in zip(render.bounds_minimum, render.bounds_maximum))
    return PreparedSkeletonDebug((0,), (centre,), ())


def export_glb(model: StaticModel, path, name: str, *, camera=None) -> dict:
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
            accessor["min"] = [float(min(v[i] for v in values)) for i in range(components)]
            accessor["max"] = [float(max(v[i] for v in values)) for i in range(components)]
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
        material = {"extras": {"billboard_center": batch.billboard_center, "dk64_rdp_material": batch.material.json(), "material_policy": "glTF approximation; RDP mux retained as metadata"}, "name": f"material_{len(materials)}", "doubleSided": batch.double_sided,
                    "alphaMode": batch.alpha_mode,
                    "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0,
                                              "baseColorFactor": [1., 1., 1., mesh_decoder.rdp.opaque_alpha(batch.material)]}}
        if batch.alpha_mode == "MASK":
            material["alphaCutoff"] = 0.5
        if batch.texture1_index is not None:
            attributes["TEXCOORD_1"] = add_accessor([render.uvs1[i] for i in corners], "VEC2", 2)
            material["extras"]["dk64_secondary_texture"] = image_of[batch.texture1_index]
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
    if camera is not None:
        document["cameras"] = [{"type": "perspective", "perspective": {
            "yfov": float(np.deg2rad(45)), "znear": max(camera.scene_radius*0.005, 0.01)}}]
        document["nodes"].append({"name": "Preview camera", "camera": 0,
            "matrix": np.linalg.inv(np.asarray(camera.view_matrix())).flatten(order="F").tolist()})
        document["scenes"][0]["nodes"].append(1)
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
