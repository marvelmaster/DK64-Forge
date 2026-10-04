"""Structurally compatible clip preview for arbitrary regular actor skeletons.

No ownership is inferred from a matching channel count. Every selected clip's
complete interior interval is validated by the existing animation reader.
"""
from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import struct
import numpy as np
from .characters import CharacterSpec
from .animations import descriptor_from_row, sample_compatible_animation, descriptor_sort_key
from .core import rom_model, skeleton, animation_census, bone_matrix, pose_gltf, texture_bank


class ActorAnimations:
    def __init__(self, rom, entry, model, assets):
        actor = rom_model.parse_actor(texture_bank.table_entry(rom, 5, entry))
        sk = skeleton.parse_actor_skeleton(actor)
        count = len(sk.bones)
        spec = CharacterSpec("actor", "Actor", entry, entry, 0, 0, -1, count, 0,
                             model.triangles, 0, 0, 0, 0)
        spec = replace(spec, channel_bones=max(b.master_index for b in sk.bones)+1)
        from .characters import CHARACTERS, INSTRUMENT_ENTRIES, LOW_POLY_ENTRIES
        from . import pipeline
        from .core import anim_code_table
        kong = next((k for k in CHARACTERS.values() if entry in
            (k.table5_entry, INSTRUMENT_ENTRIES[k.key], LOW_POLY_ENTRIES[k.key])), None)
        kong_routes, labels = {}, {}
        if kong is not None:
            spec = replace(kong, table5_entry=entry)
            table = anim_code_table.parse_anim_code(texture_bank.table_entry(rom, 13, 0))
            kong_routes = anim_code_table.character_clip_routes(table, kong.table13_column)
            labels = pipeline.dk_animation_names.labels_for(table, kong.table13_column)
        self.source = SimpleNamespace(normalized=rom, actor=actor, skeleton=sk, character=spec)
        self.render = model.render
        self.normals = np.asarray(model.normals)
        self.joints = np.asarray(model.rigid_joints) if model.triangles else np.zeros(len(model.render.positions), dtype=int)
        if not model.triangles:
            self.normals = np.zeros((len(model.render.positions),3))
        if len(self.joints) != len(self.render.positions) or any(self.joints >= count):
            raise ValueError("Actor mesh joint assignments exceed skeleton")
        self.inverse_binds = []
        for bone in sk.bones:
            inverse = np.eye(4)
            inverse[:3, 3] = -np.asarray(bone.global_translation)
            self.inverse_binds.append(inverse)
        from .actor_routes import confirmed_routes, known_clip_names
        routes = confirmed_routes(rom, entry)
        known_names = known_clip_names(rom)
        self.descriptors = []
        records = actor.data[actor.bone_start:actor.bone_start + count * 16]
        quarter = bone_matrix.quarter_table_words_from_rom(rom)
        for index, asset, info in assets:
            if len(asset) < 20 or animation_census._prefix_layout(asset)["descriptor_output_count"] != (max(b.master_index for b in sk.bones)+1) * 3:
                continue
            row = animation_census.analyze_asset(asset, records, quarter, bone_count=count, field_prefix=spec.census_prefix)
            row.update(info, id=index)
            try:
                descriptor = descriptor_from_row(row, kong_routes, spec, labels)
            except ValueError:
                continue
            if index in routes:
                label, evidence = routes[index]
                descriptor = replace(descriptor, label=f"{label} · {index:04X}",
                    ownership="ACTOR_OWNERSHIP_VERIFIED_STATIC_SOURCE", semantic_evidence=evidence)
            elif not descriptor.owned:
                if index in known_names:
                    name, evidence = known_names[index]
                    descriptor = replace(descriptor, label=f"Other actor · {name} · {index:04X}",
                        semantic_evidence=f"Name from another actor; ownership for this model remains unknown. {evidence}")
                else:
                    descriptor = replace(descriptor, label=f"Unassigned animation · {index:04X}")
            self.descriptors.append(descriptor)
        self.descriptors.sort(key=descriptor_sort_key)
        self.samples = ()
        self._matrix_cache = {}

    def select(self, index):
        self.samples = ()
        self._matrix_cache = {}
        self.selected_id = None
        descriptor = next(d for d in self.descriptors if d.table11_id == index)
        self.samples, _ = sample_compatible_animation(self.source, descriptor)
        self.selected_id = index
        return descriptor

    def matrices(self, frame):
        from .interpolation import blend_matrix
        if frame in self._matrix_cache:
            return self._matrix_cache[frame]
        first = int(frame) % len(self.samples)
        fraction = frame-int(frame)
        a = [np.asarray(pose_gltf.compact_local_to_gltf_matrix(words)).reshape(4,4) for words in self.samples[first].composed_words]
        if fraction:
            b = [np.asarray(pose_gltf.compact_local_to_gltf_matrix(words)).reshape(4,4) for words in self.samples[(first+1)%len(self.samples)].composed_words]
            a = [blend_matrix(x,y,fraction) for x,y in zip(a,b)]
        if len(self._matrix_cache) >= 4:
            self._matrix_cache.clear()
        self._matrix_cache[frame] = a
        return a

    def pose(self, frame):
        points = np.asarray(self.render.positions)
        result = points.copy()
        for joint, composed in enumerate(self.matrices(frame)):
            mask = self.joints == joint
            matrix = composed @ self.inverse_binds[joint]
            result[mask] = points[mask] @ matrix[:3, :3].T + matrix[:3, 3]
        return tuple(tuple(float(v) for v in p) for p in result)

    def posed_normals(self, frame):
        if len(self.normals) != len(self.render.positions):
            return None
        result = self.normals.copy()
        for joint, composed in enumerate(self.matrices(frame)):
            matrix = composed[:3,:3]
            mask = self.joints == joint
            result[mask] = self.normals[mask] @ np.linalg.pinv(matrix)
        return result

    def colors(self, frame, transform=None):
        normals = self.posed_normals(frame)
        colors = np.asarray(self.render.colors).copy()
        if normals is None:
            return tuple(map(tuple, colors))
        if transform is not None:
            normals = normals @ np.linalg.pinv(transform).T
        lengths = np.linalg.norm(normals, axis=1)
        lit = lengths > 1e-8
        light = np.asarray((.35,.8,.5)); light /= np.linalg.norm(light)
        shade = .55 + .45 * np.maximum(0, normals[lit] @ light / lengths[lit])
        colors[lit, :3] = shade[:, None]
        return tuple(map(tuple, colors))

    def export_glb(self, model, path, name):
        """Export the selected interior clip with the mesh's rigid ROM joints."""
        if not self.samples:
            raise ValueError("Select a fully sampled actor clip first")
        from . import static_model
        from .core.entry4_preview import convert_samples_to_joint_trs
        actor = self.source.actor
        records = actor.data[actor.bone_start:actor.bone_start + len(self.inverse_binds) * 16]
        records = b"".join(sorted((records[i:i+16] for i in range(0,len(records),16)), key=lambda row:row[1]))
        transforms, _ = convert_samples_to_joint_trs(self.samples, records, allow_constant=True,
                                                     relative_global_tolerance=4 * 2**-23,
                                                     channel_count=self.source.character.channels // 3)
        with TemporaryDirectory(prefix="dk64_actor_") as folder:
            temporary = Path(folder) / "mesh.glb"
            static_model.export_glb(model, temporary, name)
            data = temporary.read_bytes()
        length = struct.unpack_from("<I", data, 12)[0]
        doc = json.loads(data[20:20 + length])
        binary_length = struct.unpack_from("<I", data, 20 + length)[0]
        binary = bytearray(data[28 + length:28 + length + binary_length])

        def accessor(rows, kind, fmt, component=5126):
            while len(binary) % 4:
                binary.append(0)
            payload = b"".join(struct.pack(fmt, *row) for row in rows)
            view = len(doc["bufferViews"])
            doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(payload)})
            binary.extend(payload)
            entry = {"bufferView": view, "componentType": component, "count": len(rows), "type": kind}
            if kind == "SCALAR":
                entry.update(min=[min(row[0] for row in rows)], max=[max(row[0] for row in rows)])
            doc["accessors"].append(entry)
            return len(doc["accessors"]) - 1

        for primitive, batch in zip(doc["meshes"][0]["primitives"], model.render.batches):
            indices = self.joints[batch.first_vertex:batch.first_vertex + batch.vertex_count]
            primitive["attributes"]["JOINTS_0"] = accessor([(int(i), 0, 0, 0) for i in indices], "VEC4", "<4H", 5123)
            primitive["attributes"]["WEIGHTS_0"] = accessor([(1., 0., 0., 0.)] * len(indices), "VEC4", "<4f")
        doc["nodes"][0]["skin"] = 0
        for bone in self.source.skeleton.bones:
            node = {"name": f"bone_{bone.index:02d}", "translation": bone.local_translation}
            if bone.children:
                node["children"] = [i + 1 for i in bone.children]
            doc["nodes"].append(node)
        doc["scenes"][0]["nodes"].append(1)
        matrices = [tuple(matrix.flatten(order="F")) for matrix in self.inverse_binds]
        doc["skins"] = [{"joints": list(range(1, len(matrices) + 1)), "skeleton": 1,
                         "inverseBindMatrices": accessor(matrices, "MAT4", "<16f")}]
        times = accessor([(i / 30.,) for i in range(len(transforms))], "SCALAR", "<f")
        animation = {"name": "Actor compatible clip", "samplers": [], "channels": [],
                     "extras": {"table11_id": self.selected_id, "ownership": next(d.ownership if d.owned else "UNKNOWN" for d in self.descriptors if d.table11_id == self.selected_id),
                                "runtime_faithful": False, "timing": "diagnostic 30 units/s",
                                "adjustments_applied": False, "endpoint_loop_policy": "unknown"}}
        for joint in range(len(matrices)):
            for path_name, kind, fmt in (("translation", "VEC3", "<3f"), ("rotation", "VEC4", "<4f"), ("scale", "VEC3", "<3f")):
                output = accessor([getattr(frame[joint], path_name) for frame in transforms], kind, fmt)
                animation["channels"].append({"sampler": len(animation["samplers"]), "target": {"node": joint + 1, "path": path_name}})
                animation["samplers"].append({"input": times, "output": output, "interpolation": "LINEAR"})
        doc["animations"] = [animation]
        if not model.triangles:
            doc.pop("meshes", None)
            doc["nodes"][0].pop("mesh", None)
            doc["nodes"][0].pop("skin", None)
        doc["buffers"][0]["byteLength"] = len(binary)
        text = json.dumps(doc, separators=(",", ":")).encode("utf-8")
        text += b" " * (-len(text) % 4)
        binary += b"\x00" * (-len(binary) % 4)
        total = 12 + 8 + len(text) + 8 + len(binary)
        Path(path).write_bytes(struct.pack("<III", 0x46546C67, 2, total) +
                               struct.pack("<II", len(text), 0x4E4F534A) + text +
                               struct.pack("<II", len(binary), 0x004E4942) + binary)
        return {"bones": len(matrices), "samples": len(transforms), "triangles": model.triangles}

    def skeleton_debug(self, frame):
        from .debug_view import PreparedSkeletonDebug
        positions = tuple(tuple(m[:3,3]) for m in self.matrices(frame))
        edges = tuple(p for b in self.source.skeleton.bones if b.parent_index is not None
                      for p in (positions[b.parent_index],positions[b.index]))
        return PreparedSkeletonDebug(tuple(range(len(positions))),positions,edges)


def animation_assets(rom):
    return tuple((index, *rom_model.extract_entry(rom, 11, index))
                 for index in range(texture_bank.entry_count(rom, 11)))
