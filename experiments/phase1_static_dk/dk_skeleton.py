"""DK64 actor skeleton parsing and bind-pose glTF experiment.

This module consumes the actor representation from static_dk.py. ROM-derived
meshes and glTF output belong under the experiment's ignored local_output tree.
"""

from __future__ import annotations

import json
import math
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from static_dk import Actor, Mesh, ModelError, need


@dataclass(frozen=True)
class ActorBone:
    index: int
    record_offset: int
    parent_index: int | None
    local_index: int
    master_index: int
    reserved: int
    local_translation: tuple[float, float, float]
    global_translation: tuple[float, float, float]
    depth: int
    children: tuple[int, ...]


@dataclass(frozen=True)
class ActorSkeleton:
    bones: tuple[ActorBone, ...]
    roots: tuple[int, ...]

    def report(self) -> dict:
        return {
            "bone_count": len(self.bones),
            "roots": list(self.roots),
            "transform_model": "translation-only; identity local rotation and scale",
            "bones": [
                {
                    "index": b.index,
                    "record_offset": f"0x{b.record_offset:04X}",
                    "parent_index": b.parent_index,
                    "local_index": b.local_index,
                    "master_index": b.master_index,
                    "reserved_byte": b.reserved,
                    "local_translation": list(b.local_translation),
                    "global_translation": list(b.global_translation),
                    "depth": b.depth,
                    "children": list(b.children),
                }
                for b in self.bones
            ],
        }


def parse_actor_skeleton(actor: Actor) -> ActorSkeleton:
    """Decode the 0x10-byte actor bone records and validate the hierarchy."""
    count = actor.header["bone_count"]
    need(count == len(actor.bones), "ActorModelHeader bone count differs from parsed table")
    need(actor.bone_start + count * 0x10 <= len(actor.data), "bone table is out of asset bounds")

    rows = []
    children: dict[int, list[int]] = defaultdict(list)
    roots = []
    for index in range(count):
        offset = actor.bone_start + index * 0x10
        parent_raw, local_index, master_index, reserved = actor.data[offset:offset + 4]
        local = struct.unpack_from(">fff", actor.data, offset + 4)
        need(all(math.isfinite(value) for value in local), f"nonfinite local bone offset {index}")
        need(local_index == index, f"bone {index} local index {local_index} does not match row")
        need(master_index < count, f"bone {index} master index is out of range")
        parent = None if parent_raw == 0xFF else parent_raw
        if parent is None:
            roots.append(index)
        else:
            need(parent < count, f"bone {index} parent {parent} is out of range")
            need(parent != index, f"bone {index} is its own parent")
            children[parent].append(index)
        rows.append((offset, parent, local_index, master_index, reserved, local))

    state = [0] * count
    globals_: list[tuple[float, float, float] | None] = [None] * count
    depths = [0] * count

    def visit(index: int) -> tuple[float, float, float]:
        if state[index] == 1:
            raise ModelError(f"bone hierarchy cycle at bone {index}")
        if state[index] == 2:
            return globals_[index]  # type: ignore[return-value]
        state[index] = 1
        _offset, parent, _local_index, _master, _reserved, local = rows[index]
        if parent is None:
            accumulated = local
            depths[index] = 0
        else:
            parent_global = visit(parent)
            accumulated = tuple(parent_global[axis] + local[axis] for axis in range(3))
            depths[index] = depths[parent] + 1
        globals_[index] = accumulated
        state[index] = 2
        return accumulated

    for index in range(count):
        visit(index)
    need(roots, "skeleton has no root")
    need(all(value == 2 for value in state), "not every bone is reachable from a root")

    bones = tuple(
        ActorBone(index, row[0], row[1], row[2], row[3], row[4], row[5],
                  globals_[index], depths[index], tuple(children[index]))
        for index, row in enumerate(rows)
    )
    return ActorSkeleton(bones, tuple(roots))


def translation_matrix(translation: tuple[float, float, float]) -> tuple[float, ...]:
    x, y, z = translation
    return (1.0, 0.0, 0.0, x,
            0.0, 1.0, 0.0, y,
            0.0, 0.0, 1.0, z,
            0.0, 0.0, 0.0, 1.0)


def multiply_mat4(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(sum(a[row * 4 + k] * b[k * 4 + col] for k in range(4))
                 for row in range(4) for col in range(4))


def inverse_bind_matrix(bone: ActorBone) -> tuple[float, ...]:
    return translation_matrix(tuple(-value for value in bone.global_translation))


def _transform_point(matrix: tuple[float, ...], point: tuple[float, float, float]):
    x, y, z = point
    return (
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
    )


def analyze_bind_pose(actor: Actor, mesh: Mesh, skeleton: ActorSkeleton) -> dict:
    """Check source-local + accumulated bind offset against Phase 1 positions."""
    need(len(mesh.positions) == len(mesh.refs), "mesh position/ref counts differ")
    errors = []
    source_joint_contexts: dict[int, set[int]] = defaultdict(set)
    per_joint_output = Counter()
    for output_index, ref in enumerate(mesh.refs):
        need(0 <= ref.bone < len(skeleton.bones), f"vertex uses invalid joint {ref.bone}")
        at = actor.vertices_start + ref.source * 16
        raw = struct.unpack_from(">hhh", actor.data, at)
        bind = tuple(float(raw[axis]) + skeleton.bones[ref.bone].global_translation[axis]
                     for axis in range(3))
        canonical = mesh.positions[output_index]
        errors.append(math.dist(bind, canonical))
        source_joint_contexts[ref.source].add(ref.bone)
        per_joint_output[ref.bone] += 1

    inverse_errors = []
    for bone in skeleton.bones:
        product = multiply_mat4(translation_matrix(bone.global_translation), inverse_bind_matrix(bone))
        inverse_errors.extend(abs(value - expected) for value, expected in
                              zip(product, (1.0, 0.0, 0.0, 0.0,
                                            0.0, 1.0, 0.0, 0.0,
                                            0.0, 0.0, 1.0, 0.0,
                                            0.0, 0.0, 0.0, 1.0)))

    contexts = {source: sorted(joints) for source, joints in source_joint_contexts.items()
                if len(joints) > 1}
    max_error = max(errors, default=0.0)
    average_error = sum(errors) / len(errors) if errors else 0.0
    worst = sorted(enumerate(errors), key=lambda row: row[1], reverse=True)[:8]
    actor_vertices = actor.vertex_count
    loaded_count = mesh.stats["loaded_source_vertices"]
    referenced_count = mesh.stats["referenced_source_vertices"]
    return {
        "actor_vertex_block_count": actor_vertices,
        "loaded_source_vertices": loaded_count,
        "referenced_source_vertices": referenced_count,
        "exported_vertices": len(mesh.positions),
        "triangles": len(mesh.triangles),
        "bone_count": len(skeleton.bones),
        "assigned_export_vertices": len(mesh.positions),
        "unassigned_export_vertices": sum(1 for ref in mesh.refs if ref.bone < 0),
        "actor_vertices_never_loaded": actor_vertices - loaded_count,
        "loaded_but_not_triangle_referenced": loaded_count - referenced_count,
        "multi_joint_source_vertices": contexts,
        "g_mtx_command_count": mesh.stats["g_mtx_command_count"],
        "g_mtx_commands_by_bone": mesh.stats["g_mtx_commands_by_bone"],
        "g_popmtx_command_count": mesh.stats["g_popmtx_command_count"],
        "g_vtx_loads_by_bone": mesh.stats["g_vtx_loads_by_bone"],
        "g_vtx_vertex_instances_by_bone": mesh.stats["g_vtx_vertex_instances_by_bone"],
        "loaded_source_vertices_by_bone": mesh.stats["loaded_source_vertices_by_bone"],
        "referenced_source_vertices_by_bone": mesh.stats["referenced_source_vertices_by_bone"],
        "unreferenced_bones": sorted(set(range(len(skeleton.bones))) -
                                       set(mesh.stats["bone_ids_encountered"])),
        "max_raw_plus_global_offset_error": max_error,
        "average_raw_plus_global_offset_error": average_error,
        "worst_vertices": [{"output_vertex": index, "error": error} for index, error in worst],
        "max_global_bind_times_inverse_bind_error": max(inverse_errors, default=0.0),
        "glb_mesh_space_policy": (
            "Store canonical model-space positions (raw source position + global bind offset); "
            "use inverse global bind matrices so every joint skin matrix is identity at rest."),
        "coordinate_conversion": "identity XYZ mapping; preserve existing Phase 1 position convention",
    }


def _append_aligned(blob: bytearray, payload: bytes) -> tuple[int, int]:
    while len(blob) % 4:
        blob.append(0)
    offset = len(blob)
    blob.extend(payload)
    return offset, len(payload)


def export_skinned_gltf(mesh: Mesh, skeleton: ActorSkeleton, out: Path) -> dict:
    """Write a rigidly weighted glTF 2.0 skin over the Phase 1 bind geometry."""
    need(bool(mesh.positions) and bool(mesh.triangles), "empty canonical DK mesh")
    need(len(mesh.positions) == len(mesh.refs), "mesh position/ref counts differ")
    need(len(mesh.normals) == len(mesh.positions), "normal count differs from mesh")
    need(len(skeleton.roots) == 1, "glTF export currently requires one skeleton root")
    need(all(0 <= ref.bone < len(skeleton.bones) for ref in mesh.refs),
         "mesh has an invalid vertex-to-joint binding")
    out.parent.mkdir(parents=True, exist_ok=True)

    blob = bytearray()
    views = []
    accessors = []

    def add_accessor(payload: bytes, component: int, kind: str, count: int,
                     target: int | None = 34962, minimum=None, maximum=None):
        offset, length = _append_aligned(blob, payload)
        view_index = len(views)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": length}
        if target is not None:
            view["target"] = target
        views.append(view)
        accessor = {"bufferView": view_index, "componentType": component,
                    "count": count, "type": kind}
        if minimum is not None:
            accessor["min"] = minimum
        if maximum is not None:
            accessor["max"] = maximum
        accessors.append(accessor)
        return len(accessors) - 1

    position_min = [min(p[axis] for p in mesh.positions) for axis in range(3)]
    position_max = [max(p[axis] for p in mesh.positions) for axis in range(3)]
    position_accessor = add_accessor(
        b"".join(struct.pack("<fff", *p) for p in mesh.positions), 5126, "VEC3",
        len(mesh.positions), minimum=position_min, maximum=position_max)
    normal_accessor = add_accessor(
        b"".join(struct.pack("<fff", *n) for n in mesh.normals), 5126, "VEC3",
        len(mesh.normals))
    joint_payload = b"".join(struct.pack("<4H", ref.bone, 0, 0, 0) for ref in mesh.refs)
    joint_accessor = add_accessor(joint_payload, 5123, "VEC4", len(mesh.refs), maximum=[len(skeleton.bones)-1, 0, 0, 0])
    weight_payload = b"".join(struct.pack("<4f", 1.0, 0.0, 0.0, 0.0) for _ in mesh.refs)
    weight_accessor = add_accessor(weight_payload, 5126, "VEC4", len(mesh.refs))
    inverse_bind_bytes = bytearray()
    for bone in skeleton.bones:
        row_major = inverse_bind_matrix(bone)
        # glTF MAT4 accessor data is column-major.
        column_major = tuple(row_major[row * 4 + col] for col in range(4) for row in range(4))
        inverse_bind_bytes.extend(struct.pack("<16f", *column_major))
    inverse_bind_accessor = add_accessor(bytes(inverse_bind_bytes), 5126, "MAT4",
                                         len(skeleton.bones), target=None)

    index_payload = b"".join(struct.pack("<III", *tri) for tri in mesh.triangles)
    index_values = [index for tri in mesh.triangles for index in tri]
    index_accessor = add_accessor(index_payload, 5125, "SCALAR", len(index_values), target=34963,
                                  minimum=[min(index_values)], maximum=[max(index_values)])

    joint_node_for_bone = {bone.index: bone.index + 1 for bone in skeleton.bones}
    nodes = [{"name": "DK_RootMesh", "mesh": 0, "skin": 0}]
    for bone in skeleton.bones:
        node = {"name": f"bone_{bone.index:02d}",
                "translation": list(bone.local_translation)}
        if bone.children:
            node["children"] = [joint_node_for_bone[index] for index in bone.children]
        nodes.append(node)

    doc = {
        "asset": {"version": "2.0", "generator": "DK64 Forge Phase 2A skeleton experiment"},
        "scene": 0,
        "scenes": [{"nodes": [0, joint_node_for_bone[skeleton.roots[0]]]}],
        "nodes": nodes,
        "meshes": [{"name": "DK actor table 5 entry 3", "primitives": [{
            "attributes": {"POSITION": position_accessor, "NORMAL": normal_accessor,
                            "JOINTS_0": joint_accessor, "WEIGHTS_0": weight_accessor},
            "indices": index_accessor, "material": 0, "mode": 4,
        }]}],
        "materials": [{"name": "DK neutral bind inspection material",
                       "pbrMetallicRoughness": {"baseColorFactor": [0.42, 0.19, 0.07, 1.0],
                                                "metallicFactor": 0.0,
                                                "roughnessFactor": 0.9},
                       "doubleSided": True}],
        "skins": [{"name": "DK64 actor skeleton", "skeleton": joint_node_for_bone[skeleton.roots[0]],
                   "joints": [joint_node_for_bone[bone.index] for bone in skeleton.bones],
                   "inverseBindMatrices": inverse_bind_accessor}],
        "buffers": [{"uri": out.with_suffix(".bin").name, "byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
    }
    out.with_suffix(".bin").write_bytes(blob)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return validate_skinned_gltf(out)


def validate_skinned_gltf(path: Path) -> dict:
    """Validate glTF skin accessors and bind matrices without Blender."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    need(doc["asset"]["version"] == "2.0", "not glTF 2.0")
    need(len(doc.get("skins", [])) == 1, "expected one glTF skin")
    blob = path.with_name(doc["buffers"][0]["uri"]).read_bytes()
    need(len(blob) == doc["buffers"][0]["byteLength"], "skin buffer length mismatch")
    skin = doc["skins"][0]
    joints = skin["joints"]
    need(len(joints) > 0 and len(set(joints)) == len(joints), "invalid skin joints")
    need(all(0 <= joint < len(doc["nodes"]) for joint in joints), "skin joint node out of range")
    ib_accessor = doc["accessors"][skin["inverseBindMatrices"]]
    need(ib_accessor["type"] == "MAT4" and ib_accessor["componentType"] == 5126 and
         ib_accessor["count"] == len(joints), "invalid inverse-bind accessor")
    primitive = doc["meshes"][0]["primitives"][0]
    attrs = primitive["attributes"]
    position_accessor = doc["accessors"][attrs["POSITION"]]
    joint_accessor = doc["accessors"][attrs["JOINTS_0"]]
    weight_accessor = doc["accessors"][attrs["WEIGHTS_0"]]
    index_accessor = doc["accessors"][primitive["indices"]]
    count = position_accessor["count"]
    need(joint_accessor["type"] == "VEC4" and joint_accessor["componentType"] == 5123 and
         joint_accessor["count"] == count, "invalid JOINTS_0 accessor")
    need(weight_accessor["type"] == "VEC4" and weight_accessor["componentType"] == 5126 and
         weight_accessor["count"] == count, "invalid WEIGHTS_0 accessor")
    need(index_accessor["type"] == "SCALAR" and index_accessor["componentType"] == 5125 and
         index_accessor["count"] % 3 == 0, "invalid triangle index accessor")

    def accessor_bytes(accessor):
        view = doc["bufferViews"][accessor["bufferView"]]
        start = view["byteOffset"] + accessor.get("byteOffset", 0)
        return blob[start:start + view["byteLength"]]

    positions = list(struct.iter_unpack("<fff", accessor_bytes(position_accessor)))
    joint_rows = list(struct.iter_unpack("<4H", accessor_bytes(joint_accessor)))
    weight_rows = list(struct.iter_unpack("<4f", accessor_bytes(weight_accessor)))
    indices = [row[0] for row in struct.iter_unpack("<I", accessor_bytes(index_accessor))]
    need(len(positions) == count and len(joint_rows) == count and len(weight_rows) == count,
         "skin vertex accessor data size mismatch")
    need(all(0 <= index < count for index in indices), "skin mesh index out of range")
    need(all(sum(weights) == 1.0 and weights[0] == 1.0 and weights[1:] == (0.0, 0.0, 0.0)
             for weights in weight_rows), "expected rigid one-joint weights")
    need(all(row[0] < len(joints) and row[1:] == (0, 0, 0) for row in joint_rows),
         "joint attribute references invalid joint")

    # Reconstruct joint globals from node parent relationships and local translations.
    parents = {}
    for parent_index, node in enumerate(doc["nodes"]):
        for child in node.get("children", []):
            need(child not in parents, f"joint node {child} has multiple parents")
            parents[child] = parent_index
    global_translations = {}

    def node_global(index, active):
        if index in active:
            raise ModelError(f"glTF joint hierarchy cycle at node {index}")
        if index in global_translations:
            return global_translations[index]
        local = tuple(doc["nodes"][index].get("translation", [0.0, 0.0, 0.0]))
        parent = parents.get(index)
        value = local if parent is None else tuple(
            a + b for a, b in zip(node_global(parent, active | {index}), local))
        global_translations[index] = value
        return value

    matrix_blob = accessor_bytes(ib_accessor)
    max_ib_error = 0.0
    for joint_ordinal, node_index in enumerate(joints):
        global_translation = node_global(node_index, set())
        column_major = struct.unpack_from("<16f", matrix_blob, joint_ordinal * 64)
        row_major = tuple(column_major[col * 4 + row] for row in range(4) for col in range(4))
        product = multiply_mat4(translation_matrix(global_translation), row_major)
        identity = (1.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0)
        max_ib_error = max(max_ib_error, *(abs(a - b) for a, b in zip(product, identity)))
    need(max_ib_error < 2e-5, f"global bind * inverse bind error {max_ib_error}")
    return {"valid": True, "vertices": count, "triangles": len(indices) // 3,
            "bones": len(joints), "max_bind_inverse_error": max_ib_error,
            "joint_assignments": len(joint_rows), "rigid_weights": True}


def write_skeleton_report(path: Path, actor: Actor, mesh: Mesh,
                          skeleton: ActorSkeleton, gltf_validation: dict) -> dict:
    report = {"actor_header": actor.header, "skeleton": skeleton.report(),
              "bind_pose": analyze_bind_pose(actor, mesh, skeleton),
              "gltf_validation": gltf_validation}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
