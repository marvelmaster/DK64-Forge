"""Retime a frozen Entry-4 preview by editing its glTF time accessor only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from entry4_playback_timing import (
    ENTRY4_DIAGNOSTIC_UNITS_PER_SECOND,
    adjusted_time_to_seconds,
)
from entry4_rootmotion_preview import validate_experimental_animation_gltf


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "local_output" / "canonical_idle" / \
    "dk_entry4_rootmotion_diagnostic_fullsafe.gltf"
DEFAULT_OUTPUT = ROOT / "local_output" / "canonical_idle" / \
    "dk_entry4_roottranslation_30hz_fullsafe.gltf"
TIMING_MAPPING = "adjusted_time / 30.0 seconds"
TIMING_BASIS = "live-validated Entry-4 unit-rate interval"


def _accessor_bytes(doc: dict, blob: bytes, accessor_index: int) -> bytes:
    accessor = doc["accessors"][accessor_index]
    view = doc["bufferViews"][accessor["bufferView"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    length = view["byteLength"]
    if start < 0 or length < 0 or start + length > len(blob):
        raise ValueError("accessor exceeds its buffer")
    return blob[start:start + length]


def _write_times(doc: dict, blob: bytearray, times: tuple[float, ...]) -> None:
    animation = doc["animations"][0]
    sampler_indices = {sampler["input"] for sampler in animation["samplers"]}
    if len(sampler_indices) != 1:
        raise ValueError("expected one shared animation input accessor")
    accessor_index = sampler_indices.pop()
    accessor = doc["accessors"][accessor_index]
    if (accessor.get("type"), accessor.get("componentType"), accessor.get("count")) != (
        "SCALAR", 5126, len(times)
    ):
        raise ValueError("unexpected key-time accessor layout")
    view = doc["bufferViews"][accessor["bufferView"]]
    if view.get("byteStride") is not None or view["byteLength"] != 4 * len(times):
        raise ValueError("key-time accessor does not own a packed float32 view")
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    if start < 0 or start + view["byteLength"] > len(blob):
        raise ValueError("key-time accessor exceeds the buffer")
    struct.pack_into(f"<{len(times)}f", blob, start, *times)
    accessor["min"], accessor["max"] = [times[0]], [times[-1]]


def retime_frozen_preview(
    source: Path = DEFAULT_SOURCE,
    output: Path = DEFAULT_OUTPUT,
    *,
    units_per_second: float = ENTRY4_DIAGNOSTIC_UNITS_PER_SECOND,
) -> dict:
    """Copy the frozen artifact and replace only its animation input times."""
    if units_per_second != ENTRY4_DIAGNOSTIC_UNITS_PER_SECOND:
        raise ValueError("this retimed artifact is specifically the validated 30 Hz diagnostic")
    source_validation = validate_experimental_animation_gltf(source)
    source_doc = json.loads(source.read_text(encoding="utf-8"))
    if (source_validation["samples"], source_validation["bones"],
            source_validation["channels"]) != (98, 25, 75):
        raise ValueError("source is not the frozen 98-pose canonical preview")
    source_animation = source_doc["animations"][0]
    source_extras = source_animation["extras"]
    if source_extras.get("timing") != "diagnostic/artificial":
        raise ValueError("source preview does not have the expected artificial timing")
    old_bin_path = source.with_name(source_doc["buffers"][0]["uri"])
    old_blob = old_bin_path.read_bytes()
    input_index = source_animation["samplers"][0]["input"]
    input_accessor = source_doc["accessors"][input_index]
    input_view = source_doc["bufferViews"][input_accessor["bufferView"]]
    input_start = input_view.get("byteOffset", 0) + input_accessor.get("byteOffset", 0)
    old_times = struct.unpack_from("<98f", old_blob, input_start)
    if old_times != tuple(float(i) for i in range(98)):
        raise ValueError("source key times differ from adjusted integer times 0..97")
    new_times = tuple(struct.unpack("<f", struct.pack("<f", adjusted_time_to_seconds(
        adjusted, units_per_second=units_per_second
    )))[0] for adjusted in range(98))
    if any(b <= a for a, b in zip(new_times, new_times[1:])):
        raise ValueError("retimed keys are not strictly increasing")

    doc = json.loads(json.dumps(source_doc))
    blob = bytearray(old_blob)
    _write_times(doc, blob, new_times)
    extras = doc["animations"][0]["extras"]
    extras.update({
        "adjustments_applied": False,
        "actor_world_transform_applied": False,
        "animation_prefix_translation_applied": True,
        "runtime_faithful": False,
        "timing": "diagnostic/artificial",
        "display_time_mapping": TIMING_MAPPING,
        "timing_mapping": TIMING_MAPPING,
        "timing_basis": TIMING_BASIS,
        "endpoint_loop_policy": "unknown",
    })
    doc["asset"].setdefault("extras", {}).update({
        "runtime_faithful": False,
        "timing": "diagnostic/artificial",
        "timing_mapping": TIMING_MAPPING,
        "timing_basis": TIMING_BASIS,
        "endpoint_loop_policy": "unknown",
        "adjustments_applied": False,
        "actor_world_transform_applied": False,
        "animation_prefix_translation_applied": True,
    })
    doc["buffers"][0] = {"uri": output.with_suffix(".bin").name,
                          "byteLength": len(blob)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".bin").write_bytes(blob)
    output.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    source_trace_path = source.with_name(source.stem + "_trace.json")
    output_trace_path = output.with_name(output.stem + "_trace.json")
    trace = json.loads(source_trace_path.read_text(encoding="utf-8"))
    trace["display_time_mapping"] = TIMING_MAPPING
    trace["timing_mapping"] = TIMING_MAPPING
    trace["timing_basis"] = TIMING_BASIS
    trace["endpoint_loop_policy"] = "unknown"
    trace["runtime_faithful"] = False
    trace["display_duration_seconds"] = new_times[-1]
    if len(trace.get("samples", [])) != 98 or len(trace.get("successive_pairs_changed", [])) != 97:
        raise ValueError("source trace does not contain the verified full-safe pose sequence")
    output_trace_path.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")

    validation = validate_experimental_animation_gltf(output)
    new_blob = output.with_suffix(".bin").read_bytes()
    old_outputs = {
        (channel["target"]["node"], channel["target"]["path"]):
        _accessor_bytes(source_doc, old_blob,
                        source_animation["samplers"][channel["sampler"]]["output"])
        for channel in source_animation["channels"]
    }
    new_animation = doc["animations"][0]
    new_outputs = {
        (channel["target"]["node"], channel["target"]["path"]):
        _accessor_bytes(doc, new_blob,
                        new_animation["samplers"][channel["sampler"]]["output"])
        for channel in new_animation["channels"]
    }
    if old_outputs != new_outputs:
        raise ValueError("retiming changed one or more serialized TRS output accessors")
    expected_blob = bytearray(old_blob)
    struct.pack_into(f"<{len(new_times)}f", expected_blob, input_start, *new_times)
    if bytes(expected_blob) != bytes(new_blob):
        raise ValueError("binary buffer differs outside the key-time accessor")
    if trace.get("samples") != json.loads(source_trace_path.read_text(encoding="utf-8")).get("samples"):
        raise ValueError("trace pose samples changed during retiming")
    return {
        **validation,
        "output": str(output),
        "binary": str(output.with_suffix(".bin")),
        "trace": str(output_trace_path),
        "timing_mapping": TIMING_MAPPING,
        "timing_basis": TIMING_BASIS,
        "pose_output_accessors_byte_identical": True,
        "binary_diff_only_key_time_accessor": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(retime_frozen_preview(args.source, args.out), indent=2))


if __name__ == "__main__":
    main()
