"""Package an animated actor attachment into a character glTF without losing its skin."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import struct
from . import static_model


def add_bongos(source, path):
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    model = static_model.actor_model(source.normalized, 0xA5, static_model.TextureCache(source.normalized))
    if model is None:
        raise ValueError("DK bongos actor could not be decoded")
    with TemporaryDirectory(prefix="dk64_bongos_") as folder:
        glb = Path(folder) / "bongos.glb"
        if doc.get("animations"):
            from .bongos import animation
            clips = animation(source.normalized, model)
            clips.export_glb(model, glb, "DK bongos table 5 entry A5")
        else:
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
    blob_path = path.with_name(doc["buffers"][0]["uri"])
    blob = bytearray(blob_path.read_bytes())
    while len(blob) % 4:
        blob.append(0)
    offset = len(blob)
    bases = {key: len(doc.get(key, [])) for key in
             ("bufferViews", "accessors", "images", "samplers", "textures", "materials", "meshes", "nodes", "skins")}
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
    for mesh in attachment["meshes"]:
        for primitive in mesh["primitives"]:
            primitive["attributes"] = {key: value + bases["accessors"] for key, value in primitive["attributes"].items()}
            primitive["material"] += bases["materials"]
    for node in attachment["nodes"]:
        if "mesh" in node:
            node["mesh"] += bases["meshes"]
        if "skin" in node:
            node["skin"] += bases["skins"]
        if "children" in node:
            node["children"] = [i + bases["nodes"] for i in node["children"]]
    for skin in attachment.get("skins", []):
        skin["joints"] = [i + bases["nodes"] for i in skin["joints"]]
        skin["skeleton"] += bases["nodes"]
        skin["inverseBindMatrices"] += bases["accessors"]
    root = len(doc["nodes"]) + len(attachment["nodes"])
    # moveAndScaleActorToAnother copies actor placement, not skeletal motion.
    doc["scenes"][0]["nodes"].append(root)
    for animation in attachment.get("animations", []):
        animation["name"] = "DK bongos source script 299 (diagnostic timing)"
        animation["extras"].update(ownership="SOURCE VERIFIED", table13_script=0x299,
                                    table11_sequence=list(clips.clip_sequence),
                                    timing="concatenated validated interior samples, 30 units/s; script waits/speed changes unresolved")
        for sampler in animation["samplers"]:
            sampler["input"] += bases["accessors"]
            sampler["output"] += bases["accessors"]
        for channel in animation["channels"]:
            channel["target"]["node"] += bases["nodes"]
        # Append to an existing character animation so both play on one clock.
        if doc.get("animations"):
            target = doc["animations"][0]
            base = len(target["samplers"])
            for channel in animation["channels"]:
                channel["sampler"] += base
            target["samplers"].extend(animation["samplers"])
            target["channels"].extend(animation["channels"])
            target.setdefault("extras", {})["bongos"] = animation["extras"]
        else:
            doc.setdefault("animations", []).append(animation)
    for key in bases:
        doc.setdefault(key, []).extend(attachment.get(key, []))
    doc["nodes"].append({"name": "DK bongos attachment", "scale": [1.25]*3,
                         "children": [i + bases["nodes"] for i in attachment["scenes"][0]["nodes"]],
                         "extras": {"attachment_source": "806F10E8; BONGOS actor 239 -> 8069E040 -> script 299"}})
    blob.extend(binary)
    doc["buffers"][0]["byteLength"] = len(blob)
    blob_path.write_bytes(blob)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return model.triangles
