"""GUI-facing commands using the established static and Entry-4 exporters."""

from dataclasses import dataclass
from enum import Enum
import json
import struct
from pathlib import Path
from tempfile import TemporaryDirectory

from . import pipeline
from .animations import (DIAGNOSTIC_UNITS_PER_SECOND, AnimationDescriptor,
                         descriptor_for, sample_compatible_animation)
from .characters import DK
from .combine import GENERATED_UV_METADATA, combine_partial_textures, generated_uv_metadata
from .session import RomSource


class ExportKind(Enum):
    ANIMATED = "animated"
    STATIC_TEXTURED = "static_textured"
    ANIMATION_ONLY = "animation_only"  # skeleton + clip, no mesh/materials (JFG "Current Animation")


@dataclass(frozen=True)
class ExportResult:
    path: Path
    kind: ExportKind
    validation: dict


def _generic_animation(source: RomSource, descriptor: AnimationDescriptor,
                       temp: Path) -> tuple[Path, dict, int]:
    """Serialize through the established pose/glTF path, then set diagnostic times."""
    preview = pipeline.entry4_rootmotion_preview
    samples, bone_records = sample_compatible_animation(source, descriptor)
    transforms, conversion = preview.convert_samples_to_joint_trs(
        samples, bone_records, allow_constant=True,
        relative_global_tolerance=4 * 2**-23)
    character = source.character
    bind = temp / "dk_skeleton_bind.gltf"
    pipeline.dk_skeleton.export_skinned_gltf(source.mesh, source.skeleton, bind)
    unretimed = temp / "browser_unretimed.gltf"
    preview.export_experimental_animation(
        samples, transforms, unretimed, bind,
        expected_mesh=(character.vertices, character.triangles, character.bones))
    doc = json.loads(unretimed.read_text(encoding="utf-8"))
    blob = bytearray(unretimed.with_suffix(".bin").read_bytes())
    times = tuple(struct.unpack("<f", struct.pack("<f", i / DIAGNOSTIC_UNITS_PER_SECOND))[0]
                  for i in range(descriptor.sample_count))
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError(f"entry {descriptor.table11_id:04X}: invalid browser key times")
    pipeline.retime_entry4_preview._write_times(doc, blob, times)
    metadata = {
        "character": character.name,
        "table11_id": descriptor.table11_id,
        "display_label": descriptor.label,
        "semantic_label": (descriptor.label.split(" — ", 1)[1]
                           if descriptor.semantic_evidence != "unknown" else None),
        "evidence_level": descriptor.ownership,
        "ownership_evidence": descriptor.ownership,
        "structural_compatibility": descriptor.structural_compatibility,
        "semantic_evidence": descriptor.semantic_evidence,
        "dk_table13_slots": list(descriptor.dk_slots),
        "dk_table13_play_slots": list(descriptor.dk_play_slots),
        "animation_asset_decompressed_sha256": descriptor.decompressed_sha256,
        "animation_asset_decompressed_size": descriptor.decompressed_size,
        "timing": "diagnostic/artificial",
        "timing_mapping": "adjusted_time / 30.0 seconds",
        "display_time_mapping": "adjusted_time / 30.0 seconds",
        "timing_basis": descriptor.timing_basis,
        "prefix_scalar_basis": descriptor.prefix_scalar_basis,
        "safe_sample_range": [descriptor.safe_first, descriptor.safe_last],
        "sample_marker": descriptor.marker,
        "sample_stride": descriptor.stride,
        "prefix_bases": list(descriptor.prefix_bases),
        "prefix_widths": list(descriptor.prefix_widths),
        "animation_prefix_translation_applied": True,
        "actor_world_transform_applied": False,
        "adjustments_applied": False,
        "endpoint_loop_policy": "unknown",
        "runtime_faithful": False,
    }
    animation = doc["animations"][0]
    animation["name"] = f"table11_{descriptor.table11_id:04X}_ORIGIN_CENTERED_DIAGNOSTIC"
    animation["extras"].update(metadata)
    doc["asset"].setdefault("extras", {}).update(metadata)
    doc["asset"]["generator"] = "DK64 Forge compatible Table-11 browser diagnostic"
    retimed = temp / "browser_retimed.gltf"
    doc["buffers"][0] = {"uri": retimed.with_suffix(".bin").name, "byteLength": len(blob)}
    retimed.with_suffix(".bin").write_bytes(blob)
    retimed.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    validation = preview.validate_experimental_animation_gltf(retimed)
    trace = {
        "schema": "dk64-compatible-table11-browser-diagnostic-v1",
        **metadata,
        "sample_count": len(samples),
        "bone_count": character.bones,
        "conversion": conversion,
        "successive_pairs_changed": [i for i in range(1, len(samples))
                                     if samples[i].composed_words != samples[i - 1].composed_words],
        "reader_bounds": [sample.reader_metadata for sample in samples],
        "gltf_validation": validation,
    }
    return retimed, trace, len(samples)


def _export_animation_only(source: RomSource, destination: Path, animation_id: int) -> ExportResult:
    """Joint hierarchy plus the selected clip: the animated export without mesh, skin or images.

    Retargeting tools (Blender actions, NLA) only need the joints and channels; the
    pose data is byte-identical to the full animated export.
    """
    with TemporaryDirectory(prefix="dk64_forge_anim_only_") as temp_dir:
        full = Path(temp_dir) / "full.gltf"
        full_result = export_gltf(source, full, ExportKind.ANIMATED, animation_id=animation_id)
        doc = json.loads(full.read_text(encoding="utf-8"))
        blob = full.with_name(doc["buffers"][0]["uri"]).read_bytes()
    joint_nodes = set(doc["skins"][0]["joints"])
    for node in doc["nodes"]:
        node.pop("mesh", None)
        node.pop("skin", None)
    for key in ("meshes", "skins", "materials", "images", "textures", "samplers"):
        doc.pop(key, None)
    channels = doc["animations"][0]["channels"]
    if not channels or any(channel["target"]["node"] not in joint_nodes for channel in channels):
        raise ValueError("animation-only export lost its joint targets")
    doc["asset"].setdefault("extras", {}).update({"export_kind": "animation_only",
                                                  "mesh_omitted": True})
    doc["buffers"][0] = {"uri": destination.with_suffix(".bin").name, "byteLength": len(blob)}
    destination.with_suffix(".bin").write_bytes(blob)
    destination.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    validation = {"joints": len(joint_nodes), "channels": len(channels),
                  "samples": full_result.validation.get("samples"),
                  "time_range": full_result.validation.get("time_range"),
                  "mesh_omitted": True}
    return ExportResult(destination, ExportKind.ANIMATION_ONLY, validation)


def export_gltf(source: RomSource, destination: Path, kind: ExportKind,
                *, animation_id: int = 4) -> ExportResult:
    """Create a user-selected local export, without changing research artifacts."""
    destination = Path(destination)
    if destination.suffix.lower() != ".gltf":
        raise ValueError("Choose a .gltf output file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if kind is ExportKind.ANIMATION_ONLY:
        return _export_animation_only(source, destination, animation_id)
    if kind is ExportKind.STATIC_TEXTURED:
        names = {} if source.character.is_dk and source.character.variant == "normal" else {
            "node_name": f"{source.character.display_name} (canonical pose, first dynamic frames)"}
        pipeline.static_dk.export_textured_gltf(
            source.mesh, source.actor, source.normalized, destination, **names
        )
        if not source.character.is_dk or source.character.variant != "normal":
            doc = json.loads(destination.read_text(encoding="utf-8"))
            doc["meshes"][0]["name"] = (f"{source.character.display_name} actor table 5 entry "
                                        f"{source.character.table5_entry}")
            destination.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        validation = pipeline.static_dk.validate_gltf(destination)
        if validation["triangles"] != source.character.triangles:
            raise ValueError(f"Static export topology differs from canonical {source.character.name}")
        return ExportResult(destination, kind, validation)
    if kind is not ExportKind.ANIMATED:
        raise ValueError(f"Unsupported export kind: {kind}")

    if animation_id != 4 or not source.character.is_dk:
        descriptor = descriptor_for(source, animation_id)
        with TemporaryDirectory(prefix="dk64_forge_browser_") as temp_dir:
            temp = Path(temp_dir)
            animated, trace, sample_count = _generic_animation(source, descriptor, temp)
            textured = temp / "dk_static_textured.gltf"
            pipeline.static_dk.export_textured_gltf(
                source.mesh, source.actor, source.normalized, textured)
            pipeline.static_dk.validate_gltf(textured)
            validation = combine_partial_textures(
                source, animated, textured, destination, expected_samples=sample_count)
            trace.update({"partial_texture_materials_applied": True,
                          **generated_uv_metadata(source.character),
                          "gltf_validation": validation})
            destination.with_name(destination.stem + "_trace.json").write_text(
                json.dumps(trace, indent=2) + "\n", encoding="utf-8")
        spec = source.character
        if (validation["bones"], validation["triangles"], validation["samples"],
                validation["channels"]) != (spec.bones, spec.triangles,
                                            descriptor.sample_count, spec.channels):
            raise ValueError(f"entry {animation_id:04X}: animated export failed structure checks")
        return ExportResult(destination, kind, validation)

    preview = pipeline.entry4_rootmotion_preview
    times = preview.statically_safe_integer_times(source.animation_asset)
    bone_records = source.actor.data[source.actor.bone_start:
                                     source.actor.bone_start + 25 * 0x10]
    quarter_table = pipeline.trace_one_bone.quarter_table_words_from_rom(source.normalized)
    samples = preview.sample_interior_sequence(
        source.animation_asset, bone_records, quarter_table, times
    )
    transforms, conversion = preview.convert_samples_to_joint_trs(samples, bone_records)
    with TemporaryDirectory(prefix="dk64_forge_") as temp_dir:
        temp = Path(temp_dir)
        bind = temp / "dk_skeleton_bind.gltf"
        pipeline.dk_skeleton.export_skinned_gltf(source.mesh, source.skeleton, bind)
        unretimed = temp / "entry4_unretimed.gltf"
        spec = source.character
        first_validation = preview.export_experimental_animation(
            samples, transforms, unretimed, bind,
            expected_mesh=(spec.vertices, spec.triangles, spec.bones),
        )
        trace = preview.preview_report(samples)
        trace["first_vs_final_pose"] = preview.compare_first_final_pose(samples)
        trace["display_time_mapping"] = (
            "1 native adjusted-time unit = 1 glTF second; display only"
        )
        trace["conversion"] = conversion
        trace["gltf_validation"] = first_validation
        unretimed.with_name(unretimed.stem + "_trace.json").write_text(
            json.dumps(trace, indent=2) + "\n", encoding="utf-8"
        )
        retimed = temp / "entry4_30hz.gltf"
        retime_validation = pipeline.retime_entry4_preview.retime_frozen_preview(
            unretimed, retimed
        )
        entry4 = descriptor_for(source, 4)
        retimed_doc = json.loads(retimed.read_text(encoding="utf-8"))
        entry4_metadata = {
            "table11_id": 4,
            "display_label": entry4.label,
            "semantic_label": entry4.label.split(" — ", 1)[1],
            "evidence_level": entry4.ownership,
            "ownership_evidence": entry4.ownership,
            "structural_compatibility": entry4.structural_compatibility,
            "semantic_evidence": entry4.semantic_evidence,
            "animation_asset_decompressed_sha256": entry4.decompressed_sha256,
            "animation_asset_decompressed_size": entry4.decompressed_size,
            "safe_sample_range": [0, 97],
            "sample_marker": entry4.marker,
            "sample_stride": entry4.stride,
            "prefix_bases": list(entry4.prefix_bases),
            "prefix_widths": list(entry4.prefix_widths),
            "prefix_scalar_basis": entry4.prefix_scalar_basis,
        }
        retimed_doc["animations"][0].setdefault("extras", {}).update(entry4_metadata)
        retimed_doc["asset"].setdefault("extras", {}).update(entry4_metadata)
        retimed.write_text(json.dumps(retimed_doc, indent=2) + "\n", encoding="utf-8")
        textured = temp / "dk_static_textured.gltf"
        pipeline.static_dk.export_textured_gltf(
            source.mesh, source.actor, source.normalized, textured
        )
        pipeline.static_dk.validate_gltf(textured)
        validation = combine_partial_textures(source, retimed, textured, destination)
        trace = json.loads(retimed.with_name(retimed.stem + "_trace.json").read_text(
            encoding="utf-8"))
        trace["partial_texture_materials_applied"] = True
        trace.update(GENERATED_UV_METADATA)
        trace.update(entry4_metadata)
        destination.with_name(destination.stem + "_trace.json").write_text(
            json.dumps(trace, indent=2) + "\n", encoding="utf-8"
        )
        validation["pose_output_accessors_byte_identical"] = retime_validation[
            "pose_output_accessors_byte_identical"
        ]
    if (validation["bones"], validation["triangles"],
            validation["samples"], validation["channels"]) != (25, spec.triangles, 98, 75):
        raise ValueError("Animated export differs from the verified DK reference")
    return ExportResult(destination, kind, validation)
