"""Combine the verified skinned animation with established partial DK textures.

This is glTF packaging only. Texture selection, UVs, bind geometry, joint
hierarchy, and animation channels all come from the existing research outputs.
"""

from collections import defaultdict
import copy
import json
from pathlib import Path
import shutil
import struct

from . import pipeline
from .characters import DK, CharacterSpec


def generated_uv_metadata(character: CharacterSpec = DK) -> dict:
    """G_TEXTURE_GEN faces carry UVs baked for one fixed camera (static_dk.TEXGEN_BAKE_CAMERA)."""
    return {
        "generated_uv_triangles_unresolved": 0,
        "generated_uv_triangles_baked": character.texgen_triangles,
        "generated_uv_policy": ("G_TEXTURE_GEN baked for a fixed front camera "
                                "(formula VERIFIED, camera DIAGNOSTIC ASSUMPTION); "
                                "view-dependent in game"),
    }


GENERATED_UV_METADATA = generated_uv_metadata(DK)  # historical DK constant

def _accessor_rows(doc: dict, blob: bytes, index: int, fmt: str) -> list[tuple]:
    accessor = doc["accessors"][index]
    view = doc["bufferViews"][accessor["bufferView"]]
    size = struct.calcsize(fmt)
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    if start < 0 or start + size * accessor["count"] > len(blob):
        raise ValueError("texture vertex accessor is outside its buffer")
    if view.get("byteStride") is not None:
        raise ValueError("interleaved texture vertex accessors are unsupported")
    return [struct.unpack_from(fmt, blob, start + size * i)
            for i in range(accessor["count"])]


def _joint_rows(source, textured_doc: dict, textured_blob: bytes) -> list[int]:
    """Match serialized position+normal to one established rigid joint of the character."""
    by_vertex: dict[bytes, set[int]] = defaultdict(set)
    for position, normal, ref in zip(source.mesh.positions,
                                     source.mesh.normals, source.mesh.refs):
        by_vertex[struct.pack("<6f", *(position + normal))].add(ref.bone)
    positions = _accessor_rows(textured_doc, textured_blob, 0, "<3f")
    normals = _accessor_rows(textured_doc, textured_blob, 1, "<3f")
    if len(positions) != len(normals):
        raise ValueError("textured position/normal counts differ")
    joints = []
    for index, (position, normal) in enumerate(zip(positions, normals)):
        candidates = by_vertex.get(struct.pack("<6f", *(position + normal)), set())
        if len(candidates) != 1:
            raise ValueError(f"textured vertex {index} has {len(candidates)} rigid joint matches")
        joints.append(next(iter(candidates)))
    return joints


def combine_partial_textures(source, animated: Path, textured: Path,
                             destination: Path, *, expected_samples: int = 98) -> dict:
    """Write one origin-centered animated skin with Phase 1C partial materials."""
    animated_doc = json.loads(animated.read_text(encoding="utf-8"))
    textured_doc = json.loads(textured.read_text(encoding="utf-8"))
    animated_blob = animated.with_name(animated_doc["buffers"][0]["uri"]).read_bytes()
    textured_blob = textured.with_name(textured_doc["buffers"][0]["uri"]).read_bytes()
    joints = _joint_rows(source, textured_doc, textured_blob)

    doc = copy.deepcopy(animated_doc)
    blob = bytearray(animated_blob)
    while len(blob) % 4:
        blob.append(0)
    textured_offset = len(blob)
    blob.extend(textured_blob)
    view_base = len(doc["bufferViews"])
    accessor_base = len(doc["accessors"])
    for old_view in textured_doc["bufferViews"]:
        view = copy.deepcopy(old_view)
        view["byteOffset"] = view.get("byteOffset", 0) + textured_offset
        doc["bufferViews"].append(view)
    for old_accessor in textured_doc["accessors"]:
        accessor = copy.deepcopy(old_accessor)
        accessor["bufferView"] += view_base
        doc["accessors"].append(accessor)

    def append_rows(payload: bytes, component_type: int, maximum=None) -> int:
        while len(blob) % 4:
            blob.append(0)
        view_index = len(doc["bufferViews"])
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(blob),
                                    "byteLength": len(payload), "target": 34962})
        blob.extend(payload)
        accessor = {"bufferView": view_index, "componentType": component_type,
                    "count": len(joints), "type": "VEC4"}
        if maximum is not None:
            accessor["max"] = maximum
        doc["accessors"].append(accessor)
        return len(doc["accessors"]) - 1

    joint_accessor = append_rows(
        b"".join(struct.pack("<4H", joint, 0, 0, 0) for joint in joints),
        5123, [max(joints), 0, 0, 0]
    )
    weight_accessor = append_rows(
        b"".join(struct.pack("<4f", 1.0, 0.0, 0.0, 0.0) for _ in joints), 5126
    )
    primitives = []
    for old_primitive in textured_doc["meshes"][0]["primitives"]:
        primitive = copy.deepcopy(old_primitive)
        primitive["indices"] += accessor_base
        primitive["attributes"] = {key: value + accessor_base
                                   for key, value in primitive["attributes"].items()}
        primitive["attributes"]["JOINTS_0"] = joint_accessor
        primitive["attributes"]["WEIGHTS_0"] = weight_accessor
        primitives.append(primitive)
    doc["meshes"][0]["primitives"] = primitives
    for key in ("materials", "images", "textures", "samplers"):
        doc[key] = copy.deepcopy(textured_doc[key])
    doc["asset"]["generator"] = "DK64 Forge first desktop app; verified experimental paths"
    character = getattr(source, "character", DK)
    uv_metadata = generated_uv_metadata(character)
    if not character.is_dk or character.variant != "normal":
        # The shared skin writer names DK; label other characters' mesh/node (DK unchanged).
        doc["meshes"][0]["name"] = f"{character.display_name} actor table 5 entry {character.table5_entry}"
        for node in doc["nodes"]:
            if "mesh" in node:
                node["name"] = f"{character.key.upper()}_RootMesh"
    doc["asset"].setdefault("extras", {}).update({
        "partial_texture_materials_applied": True,
        **uv_metadata,
        "dynamic_texture_policy": "first-frame static fallback",
        "character": character.name,
    })
    doc["animations"][0].setdefault("extras", {}).update({
        "partial_texture_materials_applied": True,
        **uv_metadata,
        "character": character.name,
    })
    doc["buffers"][0] = {"uri": destination.with_suffix(".bin").name,
                          "byteLength": len(blob)}

    destination.parent.mkdir(parents=True, exist_ok=True)
    for image in doc["images"]:
        image_path = Path(image["uri"])
        if image_path.is_absolute() or ".." in image_path.parts:
            raise ValueError("unexpected texture image path")
        origin = textured.parent / image_path
        target = destination.parent / image_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
    destination.with_suffix(".bin").write_bytes(blob)
    destination.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    animation_validation = pipeline.entry4_rootmotion_preview.validate_experimental_animation_gltf(destination)
    geometry_validation = pipeline.static_dk.validate_gltf(destination)
    if (geometry_validation["triangles"], geometry_validation["images_found"],
            animation_validation["bones"], animation_validation["samples"],
            animation_validation["channels"]) != (
                character.triangles, len(textured_doc["images"]), character.bones,
                expected_samples, character.channels):
        raise ValueError(f"combined {character.name} glTF failed structural validation")
    if any(p["attributes"].get("JOINTS_0") != joint_accessor or
           p["attributes"].get("WEIGHTS_0") != weight_accessor for p in primitives):
        raise ValueError("a textured primitive lost its rigid skin attributes")
    return {**geometry_validation, **animation_validation,
            "triangles": geometry_validation["triangles"],
            "images_found": geometry_validation["images_found"],
            "primitives": len(primitives), "partial_textures": True}
