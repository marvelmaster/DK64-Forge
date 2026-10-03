"""Package a static actor attachment into a character glTF without losing its skin."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import struct
from . import static_model


def add_bongos(source, path):
    model = static_model.actor_model(source.normalized, 0xA5, static_model.TextureCache(source.normalized))
    if model is None:
        raise ValueError("DK bongos actor could not be decoded")
    with TemporaryDirectory(prefix="dk64_bongos_") as folder:
        glb = Path(folder) / "bongos.glb"
        static_model.export_glb(model, glb, "DK bongos table 5 entry A5")
        data = glb.read_bytes()
    json_size, tag = struct.unpack_from("<II", data, 12)
    if tag != 0x4E4F534A:
        raise ValueError("Attachment GLB has no JSON chunk")
    attachment = json.loads(data[20:20 + json_size])
    binary_size, tag = struct.unpack_from("<II", data, 20 + json_size)
    if tag != 0x004E4942:
        raise ValueError("Attachment GLB has no binary chunk")
    binary = data[28 + json_size:28 + json_size + binary_size]
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    blob_path = path.with_name(doc["buffers"][0]["uri"])
    blob = bytearray(blob_path.read_bytes())
    while len(blob) % 4:
        blob.append(0)
    offset = len(blob)
    bases = {key: len(doc.get(key, [])) for key in
             ("bufferViews", "accessors", "images", "samplers", "textures", "materials", "meshes", "nodes")}
    for view in attachment["bufferViews"]:
        view["byteOffset"] = view.get("byteOffset", 0) + offset
    for accessor in attachment["accessors"]:
        accessor["bufferView"] += bases["bufferViews"]
    for image in attachment.get("images", []):
        image["bufferView"] += bases["bufferViews"]
    for texture in attachment.get("textures", []):
        texture["source"] += bases["images"]
        texture["sampler"] += bases["samplers"]
    for material in attachment["materials"]:
        extras = material.get("extras", {})
        if "dk64_secondary_texture" in extras:
            extras["dk64_secondary_texture"] += bases["textures"]
        texture = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if texture:
            texture["index"] += bases["textures"]
    for primitive in attachment["meshes"][0]["primitives"]:
        primitive["attributes"] = {key: value + bases["accessors"] for key, value in primitive["attributes"].items()}
        primitive["material"] += bases["materials"]
    node = attachment["nodes"][0]
    node["mesh"] += bases["meshes"]
    node["scale"] = [1.25] * 3
    node["extras"] = {"attachment_source": "code_F56F0.c: 806F10E8; spawnActor(BONGOS, A6); scale 1.25",
                      "procedural_bongo_animation": "omitted; static rest pose"}
    parent = doc["skins"][0]["joints"][0] if doc.get("skins") else doc["scenes"][0]["nodes"][0]
    doc["nodes"][parent].setdefault("children", []).append(bases["nodes"])
    for key in bases:
        doc.setdefault(key, []).extend(attachment.get(key, []))
    blob.extend(binary)
    doc["buffers"][0]["byteLength"] = len(blob)
    blob_path.write_bytes(blob)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return model.triangles
