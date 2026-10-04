"""Entry-4 origin-centered prefix-translation diagnostic preview.

The caller supplies adjusted animation time. This does not model DK64's
clock, adjustment producers, Actor/world placement, or runtime fidelity.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import struct

from .pose_compose import compose_direct_hierarchy, identity_root_words
from .root_completion import reconstruct_parent_root_words
from .entry4_timing import (
    ENTRY4_DIAGNOSTIC_UNITS_PER_SECOND,
    adjusted_time_to_seconds,
)
from .animation_timeline import entry4_cursor_for_observed_state
from .pose_gltf import (JointTRS, compact_local_to_gltf_matrix,
                             decompose_joint_local)
from .skeleton import (multiply_mat4, parse_actor_skeleton,
                         validate_skinned_gltf)
from .pose_reconstruct import reconstruct_direct_local_pose_unadjusted
from .animation_reader import reproduce_asset
from .rom_model import extract_entry, normalize_rom, parse_actor
from .bone_matrix import source_quarter_table_words


ROOT = Path(__file__).resolve().parents[2]  # repository root
ROM = ROOT / "local" / "roms" / "dk64_us.n64"
ACTOR_ASSET = ROOT / "local_output" / "canonical_idle" / "dk_actor_table5_entry3.bin"
ASSEMBLY = ROOT / "upstream" / "dk64_decomp" / "src" / "global_asm" / "code_1E2D0.s"
OUTPUT = ROOT / "local_output" / "canonical_idle" / "dk_entry4_rootmotion_diagnostic.gltf"
SOURCE_SKIN = ROOT / "local_output" / "canonical_idle" / "dk_skeleton_bind.gltf"
ENTRY4_SHA256 = "3e82d9306c051926c64669222eb6dcb26884132d346312d52f51c975777a060a"
ENTRY4_OBSERVED_INTERIOR_END = 98  # exclusive; cursor 98/99 is not supported
# Diagnostic-only origin-centered parent/root reconstruction.
# The verified runtime scalar was constant in the direct Entry-4 captures.
ROOT_SCALAR_WORD = 0x38000001

IDENTITY_ACTOR_WORDS = (
    0x3F800000, 0x00000000, 0x00000000,
    0x00000000, 0x3F800000, 0x00000000,
    0x00000000, 0x00000000, 0x3F800000,
    0x00000000, 0x00000000, 0x00000000,
)


@dataclass(frozen=True)
class Entry4Sample:
    adjusted_time: float
    cursor0: int
    cursor1: int
    fraction_bits: int
    local_words: tuple[tuple[int, ...], ...]
    composed_words: tuple[tuple[int, ...], ...]
    reader_metadata: dict[str, int]
    prefix_factor_words: tuple[int, int, int]

    def report(self) -> dict[str, object]:
        payload = b"".join(struct.pack(">12I", *row) for row in self.composed_words)
        return {
            "requested_adjusted_time": self.adjusted_time,
            "cursor0": self.cursor0,
            "cursor1": self.cursor1,
            "fraction_bits": f"0x{self.fraction_bits:08X}",
            "adjustments_applied": False,
            "actor_world_transform_applied": False,
            "animation_prefix_translation_applied": True,
            "prefix_factor_words": [f"0x{word:08X}" for word in self.prefix_factor_words],
            "composed_matrix_sha256": hashlib.sha256(payload).hexdigest(),
            "composed_matrix_words": [
                [f"{word:08X}" for word in matrix] for matrix in self.composed_words
            ],
            "reader_bounds": self.reader_metadata,
        }


def _assert_finite_matrices(rows: tuple[tuple[int, ...], ...], count: int = 25) -> None:
    if len(rows) != count:
        raise ValueError(f"expected {count} matrices, got {len(rows)}")
    for index, row in enumerate(rows):
        if len(row) != 12:
            raise ValueError(f"bone {index} matrix has {len(row)} components")
        values = struct.unpack(">12f", struct.pack(">12I", *row))
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"bone {index} has nonfinite matrix components")


def origin_centered_root_words(
    local_root_words: tuple[int, ...],
    prefix_factor_words: tuple[int, int, int],
) -> tuple[int, ...]:
    """Apply verified Entry-4 prefix translation with Actor placement removed."""
    if len(prefix_factor_words) != 3:
        raise ValueError("expected exactly three animation-prefix factor words")
    return reconstruct_parent_root_words(
        local_root_words,
        IDENTITY_ACTOR_WORDS,
        prefix_factor_words,
        ROOT_SCALAR_WORD,
    )


def sample_entry4(
    adjusted_time: float,
    *,
    asset: bytes,
    bone_records: bytes,
    quarter_table: tuple[int, ...],
) -> Entry4Sample:
    """Sample one interior time with prefix translation and no Actor placement."""
    cursor = entry4_cursor_for_observed_state(
        adjusted_time, range_start=0.0, endpoint_like=98.0,
        flags=0x0003, selected_state=True,
    )
    reader, bounds = reproduce_asset(
        asset, cursor.cursor0, cursor.cursor1, cursor.fraction_bits,
        rounding_mode="nearest_even",
    )
    if len(reader.t5) != 150 or len(reader.t1) != 150:
        raise ValueError("entry-4 reader did not emit 75 t5 and 75 t1 values")
    if bounds["cursor0_end"] > len(asset) or bounds["cursor1_end"] > len(asset):
        raise ValueError("reader sample exceeds entry-4 asset")
    t5 = struct.unpack(">75H", reader.t5)
    t1 = struct.unpack(">75H", reader.t1)
    locals_ = reconstruct_direct_local_pose_unadjusted(
        bone_records, t5, t1, quarter_table,
    )
    if any(bone.adjustment_count or bone.pre_t5 != bone.post_t5 for bone in locals_):
        raise ValueError("unadjusted pose unexpectedly changed reader scratch")

    # Preserve verified animation-derived prefix translation T(u), while
    # deliberately removing captured Actor/world placement.
    prefix_factor_words = tuple(reader.fpr_bits)
    root_words = origin_centered_root_words(
        locals_[0].matrix_words,
        prefix_factor_words,
    )

    locals_ = tuple(
        replace(bone, matrix_words=root_words) if index == 0 else bone
        for index, bone in enumerate(locals_)
    )

    composed = compose_direct_hierarchy(locals_, identity_root_words())
    local_words = tuple(bone.matrix_words for bone in locals_)
    composed_words = tuple(bone.matrix_words for bone in composed)
    _assert_finite_matrices(local_words)
    _assert_finite_matrices(composed_words)
    return Entry4Sample(
        cursor.adjusted_time, cursor.cursor0, cursor.cursor1,
        cursor.fraction_bits, local_words, composed_words, bounds,
        prefix_factor_words,
    )


def load_canonical_inputs(
    rom_path: Path = ROM,
    actor_path: Path = ACTOR_ASSET,
    assembly_path: Path = ASSEMBLY,
) -> tuple[bytes, bytes, tuple[int, ...]]:
    rom, _order = normalize_rom(rom_path.read_bytes())
    asset, info = extract_entry(rom, table_id=11, index=4)
    if len(asset) != 2348 or info["decompressed_sha256"] != ENTRY4_SHA256:
        raise ValueError("table-11 entry 4 differs from the established local asset")
    actor = parse_actor(actor_path.read_bytes())
    skeleton = parse_actor_skeleton(actor)
    if len(skeleton.bones) != 25 or skeleton.roots != (0,):
        raise ValueError("canonical actor hierarchy differs from Phase 2A")
    bone_records = actor.data[actor.bone_start:actor.bone_start + 25 * 0x10]
    return asset, bone_records, source_quarter_table_words(assembly_path)


def sample_interior_sequence(
    asset: bytes, bone_records: bytes, quarter_table: tuple[int, ...],
    adjusted_times: tuple[int, ...] | None = None,
) -> tuple[Entry4Sample, ...]:
    if adjusted_times is None:
        adjusted_times = tuple(range(19))
    if not adjusted_times or any(not isinstance(value, int) for value in adjusted_times):
        raise ValueError("adjusted_times must be a nonempty sequence of integers")
    if tuple(adjusted_times) != tuple(range(adjusted_times[0], adjusted_times[-1] + 1)):
        raise ValueError("adjusted times must be one contiguous increasing interval")
    samples = tuple(sample_entry4(float(time), asset=asset,
                                  bone_records=bone_records,
                                  quarter_table=quarter_table)
                    for time in adjusted_times)
    return samples


def statically_safe_integer_times(asset: bytes) -> tuple[int, ...]:
    """Return the known interior integer interval, excluding unknown end policy.

    The observed active state's endpoint-like bound is 98 (exclusive for the
    supported interior mapping); entry 4 contains cursor samples 0..98. Thus
    time 97 reads pair 97/98, while time 98 would request 98/99.
    """
    if len(asset) != 2348 or asset[0x12] != 99 or asset[0x13] != 22:
        raise ValueError("table-11 entry 4 layout differs from established bounds")
    return tuple(range(ENTRY4_OBSERVED_INTERIOR_END))


def compare_first_final_pose(samples: tuple[Entry4Sample, ...]) -> dict[str, object]:
    first = samples[0].composed_words
    final = samples[-1].composed_words
    differences = []
    for left, right in zip(first, final):
        a = struct.unpack(">12f", struct.pack(">12I", *left))
        b = struct.unpack(">12f", struct.pack(">12I", *right))
        differences.extend(abs(x-y) for x,y in zip(a,b))
    maximum = max(differences, default=0.0)
    classification = ("identical" if maximum == 0.0 else
                      "near-identical" if maximum <= 1e-4 else
                      "clearly different")
    return {"classification": classification,
            "max_absolute_matrix_component_difference": maximum,
            "mean_absolute_matrix_component_difference":
                sum(differences) / len(differences) if differences else 0.0,
            "diagnostic_only_not_loop_evidence": True}


def preview_report(samples: tuple[Entry4Sample, ...]) -> dict[str, object]:
    distinct_pairs = [index for index in range(1, len(samples))
                      if samples[index].composed_words != samples[index - 1].composed_words]
    return {
        "schema": "dk64-entry4-origin-centered-prefix-translation-diagnostic-v1",
        "evidence_level": "VISUALLY VALIDATED EXPERIMENTAL PREVIEW; not runtime-faithful",
        "root_policy": "origin-centered; Actor/world placement removed; animation-prefix translation applied",
        "sample_count": len(samples),
        "bone_count": 25,
        "adjustments_applied": False,
        "actor_world_transform_applied": False,
        "animation_prefix_translation_applied": True,
        "runtime_faithful": False,
        "timing": "diagnostic/artificial",
        "successive_pairs_changed": distinct_pairs,
        "samples": [sample.report() for sample in samples],
    }


def convert_samples_to_joint_trs(
    samples: tuple[Entry4Sample, ...], bone_records: bytes,
    *, allow_constant: bool = False, relative_global_tolerance: float = 0.0,
    channel_count: int | None = None,
) -> tuple[tuple[tuple[JointTRS, ...], ...], dict[str, float]]:
    """Use direct parent-relative locals; compare globals to DK64 composition.

    The bone count follows the records (DK 25, Diddy 37); DK callers are unchanged.
    """
    bone_count = len(bone_records) // 16
    if not bone_count or len(bone_records) != bone_count * 16 or not samples:
        raise ValueError("expected whole bone records and a nonempty sample sequence")
    if not allow_constant and all(sample.composed_words == samples[0].composed_words for sample in samples):
        raise ValueError("all entry-4 poses are identical")
    channel_count = bone_count if channel_count is None else channel_count
    if not bone_count <= channel_count <= 128:
        raise ValueError("invalid animation channel count")
    masters = [bone_records[i * 16 + 2] for i in range(bone_count)]
    if len(set(masters)) != bone_count:
        raise ValueError("duplicate master channel")
    output: list[tuple[JointTRS, ...]] = []
    previous_rotations: list[tuple[float, float, float, float] | None] = [None] * bone_count
    max_trs_error = max_orthogonality_error = max_global_error = 0.0
    max_expected_component = 0.0
    for sample in samples:
        globals_: list[tuple[float, ...]] = []
        row: list[JointTRS] = []
        for bone_index in range(bone_count):
            record = bone_records[bone_index * 16:(bone_index + 1) * 16]
            if record[1] != bone_index or record[2] >= channel_count:
                raise ValueError(f"bone {bone_index} local/master mapping changed")
            parent = record[0]
            if (bone_index == 0 and parent != 0xFF) or (bone_index > 0 and parent >= bone_index):
                raise ValueError(f"bone {bone_index} hierarchy differs from Phase 2A")
            local_matrix = compact_local_to_gltf_matrix(sample.local_words[bone_index])
            trs = decompose_joint_local(local_matrix)
            previous = previous_rotations[bone_index]
            if previous is not None and sum(a*b for a,b in zip(previous, trs.rotation)) < 0:
                trs = JointTRS(trs.translation, tuple(-v for v in trs.rotation),
                               trs.scale, trs.reconstruction_error,
                               trs.orthogonality_error)
            previous_rotations[bone_index] = trs.rotation
            max_trs_error = max(max_trs_error, trs.reconstruction_error)
            max_orthogonality_error = max(max_orthogonality_error,
                                          trs.orthogonality_error)
            global_matrix = (local_matrix if parent == 0xFF else
                             multiply_mat4(globals_[parent], local_matrix))
            globals_.append(global_matrix)
            expected = compact_local_to_gltf_matrix(sample.composed_words[bone_index])
            max_expected_component = max(max_expected_component,
                                         *(abs(value) for value in expected))
            max_global_error = max(max_global_error,
                                   *(abs(a-b) for a,b in zip(global_matrix, expected)))
            row.append(trs)
        output.append(tuple(row))
    if max_global_error > 3e-5 + relative_global_tolerance * max_expected_component:
        raise ValueError(f"glTF parent composition error {max_global_error:.8g}")
    return tuple(output), {
        "max_local_trs_reconstruction_error": max_trs_error,
        "max_local_basis_orthogonality_error": max_orthogonality_error,
        "max_global_hierarchy_error": max_global_error,
    }


def export_experimental_animation(
    samples: tuple[Entry4Sample, ...],
    transforms: tuple[tuple[JointTRS, ...], ...],
    out: Path = OUTPUT,
    source_skin: Path = SOURCE_SKIN,
    expected_mesh: tuple[int, int, int] = (781, 704, 25),
) -> dict[str, object]:
    """Append one display-timed animation to a copy of the verified bind skin.

    expected_mesh is (vertices, triangles, bones) of the character's bind skin.
    """
    base_validation = validate_skinned_gltf(source_skin)
    if (base_validation["vertices"], base_validation["triangles"],
            base_validation["bones"]) != expected_mesh:
        raise ValueError("source bind skin differs from the canonical character mesh")
    bone_count = expected_mesh[2]
    times = tuple(sample.adjusted_time for sample in samples)
    if (not samples or len(samples) != len(transforms) or
            any(len(row) != bone_count for row in transforms)):
        raise ValueError(f"experimental animation requires complete {bone_count}-joint poses")
    if times != tuple(float(i) for i in range(int(times[0]), int(times[-1]) + 1)):
        raise ValueError("samples must be a contiguous integer-time interval")
    doc = copy.deepcopy(json.loads(source_skin.read_text(encoding="utf-8")))
    if doc.get("animations"):
        raise ValueError("source skin already contains animations")
    for bone in range(bone_count):
        if doc["nodes"][bone + 1].get("name") != f"bone_{bone:02d}":
            raise ValueError(f"source skin joint {bone} is out of order")
    blob = bytearray(source_skin.with_name(doc["buffers"][0]["uri"]).read_bytes())

    def append_accessor(payload: bytes, kind: str, count: int,
                        minimum=None, maximum=None) -> int:
        while len(blob) % 4:
            blob.append(0)
        offset = len(blob)
        blob.extend(payload)
        view_index = len(doc["bufferViews"])
        doc["bufferViews"].append({"buffer": 0, "byteOffset": offset,
                                    "byteLength": len(payload)})
        accessor = {"bufferView": view_index, "componentType": 5126,
                    "count": count, "type": kind}
        if minimum is not None:
            accessor["min"] = minimum
        if maximum is not None:
            accessor["max"] = maximum
        doc["accessors"].append(accessor)
        return len(doc["accessors"]) - 1

    sample_count = len(samples)
    time_accessor = append_accessor(struct.pack(f"<{sample_count}f", *times),
                                    "SCALAR", sample_count, [times[0]], [times[-1]])
    animation = {
        "name": "entry4_ORIGIN_CENTERED_PREFIX_TRANSLATION_DIAGNOSTIC",
        "extras": {
            "adjustments_applied": False,
            "actor_world_transform_applied": False,
            "animation_prefix_translation_applied": True,
            "runtime_faithful": False,
            "timing": "diagnostic/artificial",
            "root_policy": "origin-centered; Actor/world placement removed",
            "display_time_mapping": "1 native adjusted-time unit = 1 glTF second; display only",
            "interpolation": "LINEAR for display only; DK64 interpolation not established",
        },
        "samplers": [], "channels": [],
    }
    for bone in range(bone_count):
        for path, kind, fmt in (("translation", "VEC3", "<3f"),
                                ("rotation", "VEC4", "<4f"),
                                ("scale", "VEC3", "<3f")):
            payload = b"".join(struct.pack(fmt, *getattr(row[bone], path))
                               for row in transforms)
            output_accessor = append_accessor(payload, kind, sample_count)
            sampler_index = len(animation["samplers"])
            animation["samplers"].append({"input": time_accessor,
                                           "output": output_accessor,
                                           "interpolation": "LINEAR"})
            animation["channels"].append({"sampler": sampler_index,
                                           "target": {"node": bone + 1, "path": path}})
    doc["animations"] = [animation]
    doc["asset"]["generator"] = "DK64 Forge Entry-4 origin-centered prefix-translation diagnostic"
    doc["asset"]["extras"] = {
        "runtime_faithful": False,
        "timing": "diagnostic/artificial",
        "adjustments_applied": False,
        "actor_world_transform_applied": False,
        "animation_prefix_translation_applied": True,
    }
    doc["buffers"][0] = {"uri": out.with_suffix(".bin").name,
                         "byteLength": len(blob)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".bin").write_bytes(blob)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return validate_experimental_animation_gltf(out)


def validate_experimental_animation_gltf(path: Path) -> dict[str, object]:
    """Read the saved animation bytes and check every channel/accessor."""
    validation = validate_skinned_gltf(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if len(doc.get("animations", [])) != 1:
        raise ValueError("expected one experimental animation")
    animation = doc["animations"][0]
    extras = animation.get("extras", {})
    expected_extras = {
        "adjustments_applied": False,
        "actor_world_transform_applied": False,
        "animation_prefix_translation_applied": True,
        "runtime_faithful": False,
        "timing": "diagnostic/artificial",
    }
    if extras.get("adjustments_applied") is True and str(extras.get("procedural_hair", "")).startswith("Tiny pendulum diagnostic:"):
        expected_extras["adjustments_applied"] = True
    if any(extras.get(key) != value for key, value in expected_extras.items()):
        raise ValueError("diagnostic animation metadata is incomplete")
    timing_mapping = extras.get("timing_mapping")
    if timing_mapping is None:
        timing_kind = "artificial one-unit-per-second"
    elif timing_mapping == "adjusted_time / 30.0 seconds":
        if (extras.get("timing_basis") not in (
                "live-validated Entry-4 unit-rate interval",
                "diagnostic browser rate; unverified for this entry") or
                extras.get("endpoint_loop_policy") != "unknown" or
                extras.get("display_time_mapping") != timing_mapping):
            raise ValueError("30 Hz timing metadata is incomplete")
        timing_kind = ("live-validated 30 Hz diagnostic mapping"
                       if extras["timing_basis"] == "live-validated Entry-4 unit-rate interval"
                       else "unverified 30 Hz browser mapping")
    else:
        raise ValueError("unsupported diagnostic timing mapping")
    channel_count = 3 * len(doc["skins"][0]["joints"])
    if len(animation["channels"]) != channel_count or len(animation["samplers"]) != channel_count:
        raise ValueError("expected three animation channels per joint")
    sample_count = doc["accessors"][animation["samplers"][0]["input"]]["count"]
    blob = path.with_name(doc["buffers"][0]["uri"]).read_bytes()
    seen: set[tuple[int, str]] = set()
    for channel in animation["channels"]:
        target = channel["target"]
        node, target_path = target["node"], target["path"]
        if node not in doc["skins"][0]["joints"] or target_path not in (
            "translation", "rotation", "scale"
        ):
            raise ValueError("animation targets a non-joint or unsupported property")
        if (node, target_path) in seen:
            raise ValueError("duplicate joint animation target")
        seen.add((node, target_path))
        sampler = animation["samplers"][channel["sampler"]]
        if sampler["interpolation"] != "LINEAR":
            raise ValueError("unexpected display interpolation")
        input_accessor = doc["accessors"][sampler["input"]]
        output_accessor = doc["accessors"][sampler["output"]]
        if (input_accessor["count"], input_accessor["type"],
                input_accessor["componentType"]) != (sample_count, "SCALAR", 5126):
            raise ValueError("invalid display-time accessor")
        if (output_accessor["count"], output_accessor["type"],
                output_accessor["componentType"]) != (
                    sample_count, "VEC4" if target_path == "rotation" else "VEC3", 5126
                ):
            raise ValueError("invalid joint transform accessor")

        def values(accessor, components):
            view = doc["bufferViews"][accessor["bufferView"]]
            length = accessor["count"] * components * 4
            start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
            if view.get("byteStride") is not None or length != view["byteLength"] or \
                    start < 0 or start + length > len(blob):
                raise ValueError("animation accessor exceeds its buffer view")
            result = struct.unpack_from(f"<{accessor['count'] * components}f", blob, start)
            if not all(math.isfinite(value) for value in result):
                raise ValueError("animation accessor contains nonfinite values")
            return result

        times = values(input_accessor, 1)
        if timing_mapping is None:
            expected_times = tuple(float(i) for i in range(int(times[0]), int(times[-1]) + 1))
        else:
            expected_times = tuple(adjusted_time_to_seconds(
                i, units_per_second=ENTRY4_DIAGNOSTIC_UNITS_PER_SECOND
            ) for i in range(sample_count))
            expected_times = tuple(struct.unpack("<f", struct.pack("<f", value))[0]
                                   for value in expected_times)
        if times != expected_times or any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("display-time keys do not match the declared mapping")
        components = 4 if target_path == "rotation" else 3
        rows = values(output_accessor, components)
        if target_path == "rotation":
            for offset in range(0, len(rows), 4):
                norm = math.sqrt(sum(value*value for value in rows[offset:offset + 4]))
                if abs(norm - 1.0) > 2e-6:
                    raise ValueError("serialized joint quaternion is not normalized")
    if len(seen) != channel_count:
        raise ValueError("not every joint has translation, rotation and scale")
    input_accessor = doc["accessors"][animation["samplers"][0]["input"]]
    input_view = doc["bufferViews"][input_accessor["bufferView"]]
    input_start = input_view.get("byteOffset", 0) + input_accessor.get("byteOffset", 0)
    input_times = struct.unpack_from(f"<{sample_count}f", blob, input_start)
    return {
        **validation,
        "samples": sample_count,
        "channels": channel_count,
        "time_range": [input_times[0], input_times[-1]],
        **expected_extras,
        "timing_kind": timing_kind,
        **({"timing_mapping": timing_mapping,
            "timing_basis": extras["timing_basis"],
            "endpoint_loop_policy": extras["endpoint_loop_policy"]}
           if timing_mapping is not None else {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-safe", action="store_true",
                        help="sample every statically supported interior integer time")
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()
    asset, bone_records, quarter_table = load_canonical_inputs()
    requested = (statically_safe_integer_times(asset) if args.full_safe
                 else tuple(range(19)))
    samples = sample_interior_sequence(asset, bone_records, quarter_table, requested)
    transforms, conversion = convert_samples_to_joint_trs(samples, bone_records)
    validation = export_experimental_animation(samples, transforms, args.out)
    report = preview_report(samples)
    report["first_vs_final_pose"] = compare_first_final_pose(samples)
    report["display_time_mapping"] = "1 native adjusted-time unit = 1 glTF second; display only"
    report["conversion"] = conversion
    report["gltf_validation"] = validation
    trace = args.out.with_name(args.out.stem + "_trace.json")
    trace.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gltf": str(args.out), "trace": str(trace),
                      "samples": len(samples), "conversion": conversion,
                      "validation": validation,
                      "successive_pairs_changed": report["successive_pairs_changed"]},
                     indent=2))


if __name__ == "__main__":
    main()
