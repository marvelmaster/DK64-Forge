"""Offline structural census of DK64 US rev0 table-11 animation entries.

Compatibility means that the existing bounded reader and the verified
25-bone DK local-matrix path can consume the asset. It does not identify the
animation owner or its semantic name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]

from .animation_timeline import entry4_cursor_candidate
from .pose_gltf import compact_local_to_gltf_matrix, decompose_joint_local
from .skeleton import parse_actor_skeleton
from .entry4_preview import ENTRY4_SHA256
from .animation_reader import reproduce_asset
from .pose_reconstruct import reconstruct_direct_local_pose_unadjusted
from .rom_model import (EXPECTED_SIZE, POINTER_BASE, ModelError, be32,
                       decode_actor_mesh, extract_entry, normalize_rom, parse_actor)
from .bone_matrix import quarter_table_words_from_rom


DEFAULT_ROM = ROOT / "local" / "roms" / "dk64_us.n64"
DEFAULT_OUTPUT = ROOT / "local_output" / "dk_table11_animation_census.json"
EXPECTED_NORMALIZED_SHA256 = "b6347d9f1f75d38a88d829b4f80b1acf0d93344170a5fbe9546c484dae416ce3"
HISTORICAL_RUNTIME_IDS = {0, 1, 2, 3, 4, 9, 0x17, 0x3E, 0x3F, 0x41}
PLAYER_CALL = re.compile(
    r"playAnimation\s*\(\s*(?:gCurrentPlayer|gPlayerPointer)\s*,\s*(0x[0-9a-fA-F]+|\d+)\s*\)"
)


def candidate_sample_cursors(endpoint_marker: int) -> tuple[tuple[int, int], ...]:
    """Candidate adjacent interior pairs from the asset's endpoint-like byte.

    This is an asset-bounded sampling heuristic, not runtime endpoint policy.
    The first and last candidate pairs are validated again against actual reads.
    """
    if not 2 <= endpoint_marker <= 0x7FFF:
        return ()
    last_pair = endpoint_marker - 2
    starts = (0, last_pair // 2, last_pair)
    return tuple(dict.fromkeys((cursor, cursor + 1) for cursor in starts))


def _prefix_layout(asset: bytes) -> dict:
    if len(asset) < 0x14:
        raise ValueError("asset shorter than 0x14-byte reader header")
    header = asset[4:0x14]
    bases = tuple(int.from_bytes(header[4 + i * 2:6 + i * 2], "big", signed=True)
                  for i in range(3))
    widths = tuple(header[10:13])
    descriptor_outputs = 3 * (header[13] - 1) if header[13] else 0
    raw_offset = int.from_bytes(header[2:4], "big", signed=True)
    runtime_offset = ((raw_offset + 6 + 0x8000) & 0xFFFF) - 0x8000
    return {
        "flags": int.from_bytes(header[0:2], "big"),
        "raw_sample_offset": raw_offset,
        "loader_adjusted_sample_offset": runtime_offset,
        "prefix_bases": list(bases),
        "prefix_widths": list(widths),
        "descriptor_output_count": descriptor_outputs,
        "endpoint_like_marker": asset[0x12],
        "sample_stride": asset[0x13],
        "header_bytes_0x04_0x13": header.hex(),
        "asset_header_bytes_0x00_0x1f": asset[:0x20].hex(),
        "descriptor_start": 0x14,
    }


def _finite_trs(pose) -> tuple[float, float]:
    max_residual = 0.0
    max_shear = 0.0
    for bone in pose:
        matrix = compact_local_to_gltf_matrix(bone.matrix_words)
        if not all(math.isfinite(value) for value in matrix):
            raise ValueError("local matrix contains NaN or infinity")
        trs = decompose_joint_local(matrix)
        max_residual = max(max_residual, trs.reconstruction_error)
        max_shear = max(max_shear, trs.orthogonality_error)
    return max_residual, max_shear


def analyze_asset(asset: bytes, bone_records: bytes,
                  quarter_table: tuple[int, ...], bone_count: int = 25,
                  field_prefix: str = "dk") -> dict:
    """Classify one decompressed entry without any endpoint/wrap assumption.

    bone_count/field_prefix select the character skeleton (DK: 25, "dk");
    the reader must produce 3 outputs per referenced master scratch group.
    Physical bone count may be smaller when the master numbering has gaps.
    """
    outputs = 3 * max(bone_count, max(bone_records[2::16], default=-1) + 1)
    compat_field = f"{field_prefix}_skeleton_compatibility"
    compat_label = field_prefix.upper()
    record: dict = {
        "decompressed_size": len(asset),
        "sha256": hashlib.sha256(asset).hexdigest(),
    }
    if len(asset) < 0x14:
        record.update({"reader_compatibility": "MALFORMED / OUT_OF_BOUNDS",
                       compat_field: "UNKNOWN",
                       "failure_reason": "asset shorter than 0x14-byte reader header"})
        return record

    layout = _prefix_layout(asset)
    record["layout"] = layout
    if layout["flags"] & 0x20:
        record.update({"reader_compatibility": "READER_INCOMPATIBLE",
                       compat_field: "UNKNOWN",
                       "failure_reason": "reader flag 0x20 selects an unsupported branch"})
        return record
    if layout["descriptor_output_count"] != outputs:
        record.update({"reader_compatibility": "READER_INCOMPATIBLE",
                       compat_field: "UNKNOWN",
                       "failure_reason": (
                           "existing reader header requests "
                           f"{layout['descriptor_output_count']} outputs, expected {outputs}"
                       )})
        return record

    cursors = candidate_sample_cursors(layout["endpoint_like_marker"])
    record["candidate_sample_pairs"] = [list(pair) for pair in cursors]
    if not cursors:
        record.update({"reader_compatibility": "UNKNOWN",
                       compat_field: "UNKNOWN",
                       "failure_reason": "no interior pair from endpoint-like marker"})
        return record

    samples = []
    poses = []
    failures = []
    for cursor0, cursor1 in cursors:
        try:
            # A mid-interval read exercises interpolation. This fixed fraction
            # is only a static parser probe, not a runtime time mapping claim.
            reader, bounds = reproduce_asset(
                asset, cursor0, cursor1, 0x3F000000,
                rounding_mode="nearest_even",
            )
            if len(reader.t5) != 2 * outputs or len(reader.t1) != 2 * outputs:
                raise ValueError(
                    f"reader produced t5/t1 bytes {len(reader.t5)}/{len(reader.t1)}, "
                    f"expected {2 * outputs}/{2 * outputs}"
                )
            t5 = struct.unpack(f">{outputs}H", reader.t5)
            t1 = struct.unpack(f">{outputs}H", reader.t1)
            pose = reconstruct_direct_local_pose_unadjusted(
                bone_records, t5, t1, quarter_table, expected_bones=bone_count,
            )
            if len(pose) != bone_count:
                raise ValueError(f"{compat_label} local reconstruction produced {len(pose)} bones")
            residual, shear = _finite_trs(pose)
            poses.append(pose)
            samples.append({
                "cursor_pair": [cursor0, cursor1],
                "fraction_bits": "0x3F000000",
                "prefix_factor_raw32": [f"0x{word:08X}" for word in reader.fpr_bits],
                "t5_count": len(t5), "t1_count": len(t1),
                "local_matrix_count": len(pose),
                "reader_bounds": bounds,
                "max_trs_residual": residual,
                "max_basis_orthogonality_error": shear,
            })
        except Exception as exc:
            failures.append(f"pair {cursor0}/{cursor1}: {type(exc).__name__}: {exc}")

    record["samples"] = samples
    record["reader_compatibility"] = (
        "READER_COMPATIBLE" if samples and not failures else
        "READER_INCOMPATIBLE" if failures else "UNKNOWN"
    )
    record["sample_failures"] = failures
    record[compat_field] = (
        f"{compat_label}_SKELETON_STRUCTURALLY_COMPATIBLE"
        if samples and not failures and all(len(pose) == bone_count for pose in poses)
        else f"{compat_label}_SKELETON_INCOMPATIBLE" if samples and failures else "UNKNOWN"
    )
    if samples:
        factors = [sample["prefix_factor_raw32"] for sample in samples]
        record["prefix_summary"] = {
            "bases": layout["prefix_bases"],
            "widths": layout["prefix_widths"],
            "sampled_factor_outputs": factors,
            "sampled_constant": all(row == factors[0] for row in factors[1:]),
            "root_translation_scalar": "UNKNOWN_PER_ENTRY",
            "entry4_scalar_word_not_assumed": "0x38000001",
        }
        all_bounds = [sample["reader_bounds"] for sample in samples]
        descriptor_ends = {bounds["descriptor_end"] for bounds in all_bounds}
        record["layout"]["descriptor_region_size"] = max(descriptor_ends) - 0x14
        record["layout"]["descriptor_end_offsets_sampled"] = sorted(descriptor_ends)
    else:
        record["prefix_summary"] = {
            "bases": layout["prefix_bases"], "widths": layout["prefix_widths"],
            "sampled_constant": "UNKNOWN",
            "root_translation_scalar": "UNKNOWN_PER_ENTRY",
        }
    return record


def _generic_player_call_sites(source_root: Path) -> dict[int, list[dict[str, str | int]]]:
    """Collect player-call references; these alone do not prove DK ownership."""
    sites: dict[int, list[dict[str, str | int]]] = {}
    if not source_root.is_dir():
        return sites  # optional research cross-reference; the decomp is not required to run
    for path in source_root.rglob("*.c"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for match in PLAYER_CALL.finditer(text):
            animation_id = int(match.group(1), 0)
            line = text.count("\n", 0, match.start()) + 1
            try:
                rel = path.relative_to(ROOT).as_posix()
            except ValueError:
                rel = path.as_posix()
            sites.setdefault(animation_id, []).append({"source": rel, "line": line})
    return sites


def _table_entry_bounds(rom: bytes, table_id: int, index: int) -> tuple[int, int, int]:
    count = be32(rom, POINTER_BASE + 0x80 + table_id * 4)
    table = POINTER_BASE + be32(rom, POINTER_BASE + table_id * 4)
    if not 0 <= index < count or table < 0 or table + (count + 1) * 4 > len(rom):
        raise ModelError("pointer table entry index/bounds invalid")
    start = POINTER_BASE + (be32(rom, table + index * 4) & 0x7FFFFFFF)
    end = POINTER_BASE + (be32(rom, table + (index + 1) * 4) & 0x7FFFFFFF)
    if not 0 <= start < end <= len(rom):
        raise ModelError(f"entry bounds invalid: {start:#x}..{end:#x}")
    return count, start, end


# Per-character census parameters: (actor table-5 entry, hand mask, bones,
# triangles at that mask, model id, field prefix). DK is the verified reference.
CHARACTERS = {
    "dk": (3, 1, 25, 704, 3, "dk"),
    "diddy": (0, 0, 37, 678, 0, "diddy"),
    "tiny": (8, 1, 39, 744, 8, "tiny"),
    "chunky": (11, 1, 23, 699, 11, "chunky"),
    "lanky": (5, 1, 21, 704, 5, "lanky"),
}


def build_census(rom_path: Path = DEFAULT_ROM,
                 output_path: Path = DEFAULT_OUTPUT, character: str = "dk") -> dict:
    actor_entry, hand_state, bone_count, expected_tris, model_id, prefix = CHARACTERS[character]
    compat_field = f"{prefix}_skeleton_compatibility"
    compat_value = f"{prefix.upper()}_SKELETON_STRUCTURALLY_COMPATIBLE"
    raw = Path(rom_path).read_bytes()
    rom, byte_order = normalize_rom(raw)
    normalized_sha = hashlib.sha256(rom).hexdigest()
    if normalized_sha != EXPECTED_NORMALIZED_SHA256:
        raise ValueError(f"unsupported ROM normalized SHA-256 {normalized_sha}")
    actor_bytes, actor_info = extract_entry(rom, 5, actor_entry)
    actor = parse_actor(actor_bytes)
    skeleton = parse_actor_skeleton(actor)
    if (len(skeleton.bones) != bone_count or
            len(decode_actor_mesh(actor, hand_state=hand_state).triangles) != expected_tris):
        raise ValueError(f"canonical {character} baseline differs from the verified model")
    bone_records = actor.data[actor.bone_start:actor.bone_start + bone_count * 0x10]
    quarter_table = quarter_table_words_from_rom(rom)
    table_count = be32(rom, POINTER_BASE + 0x80 + 11 * 4)
    player_sites = _generic_player_call_sites(ROOT / "upstream" / "dk64_decomp" / "src")
    rows = []
    for animation_id in range(table_count):
        row: dict = {"id": animation_id, "table_index": animation_id}
        try:
            _count, start, end = _table_entry_bounds(rom, 11, animation_id)
            packed = rom[start:end]
            asset, extraction = extract_entry(rom, 11, animation_id)
            row.update({
                "rom_start": f"0x{start:08X}", "rom_end": f"0x{end:08X}",
                "compressed_size": len(packed),
                "compressed_sha256": hashlib.sha256(packed).hexdigest(),
                "compressed": bool(extraction["gzip"]),
                "decompressed_sha256": extraction["decompressed_sha256"],
                "decompressed_size": extraction["decompressed_size"],
            })
            row.update(analyze_asset(asset, bone_records, quarter_table, bone_count, prefix))
            row["evidence_level"] = "VERIFIED_STATIC_STRUCTURE"
        except Exception as exc:
            row.setdefault("compressed_size", None)
            row.update({
                "reader_compatibility": "MALFORMED / OUT_OF_BOUNDS",
                compat_field: "UNKNOWN",
                "failure_reason": f"{type(exc).__name__}: {exc}",
                "evidence_level": "UNKNOWN_EXTRACTION_OR_FORMAT",
            })
        sites = player_sites.get(animation_id, [])
        row["generic_player_call_sites"] = sites[:12]
        row["generic_player_call_site_count"] = len(sites)
        row["static_dk_usage"] = "NO_DK_USAGE_EVIDENCE"
        if animation_id == 4 and character == "dk":
            row["runtime_usage_evidence"] = "VERIFIED_RUNTIME_CANONICAL_DK_ENTRY4"
        elif sites:
            row["static_usage_note"] = (
                "playAnimation(gCurrentPlayer/gPlayerPointer) is generic player evidence; "
                "no DK-specific ownership established"
            )
        row["historical_runtime_id_overlap"] = animation_id in HISTORICAL_RUNTIME_IDS
        rows.append(row)

    rows.sort(key=lambda item: item["id"])
    reader_compatible = [r for r in rows if r["reader_compatibility"] == "READER_COMPATIBLE"]
    skeleton_compatible = [r for r in reader_compatible if r[compat_field] == compat_value]
    malformed = [r for r in rows if r["reader_compatibility"] == "MALFORMED / OUT_OF_BOUNDS"]
    incompatible = [r for r in rows if r["reader_compatibility"] == "READER_INCOMPATIBLE"]
    unknown = [r for r in rows if r["reader_compatibility"] == "UNKNOWN"]
    shortlist = []
    for row in skeleton_compatible:
        if row.get("runtime_usage_evidence"):
            shortlist.append({"id": row["id"], "basis": "direct canonical DK runtime evidence",
                              "additional_evidence_required": "independent repeat under a second DK state"})
        elif row["historical_runtime_id_overlap"]:
            shortlist.append({"id": row["id"], "basis": "historical runtime ID only; actor identity unproven",
                              "additional_evidence_required": "actor-identity-filtered DK runtime capture"})
    compatible_layouts = [row["layout"] for row in skeleton_compatible]
    prefix_width_counts: dict[str, int] = {}
    for layout in compatible_layouts:
        key = ",".join(str(width) for width in layout["prefix_widths"])
        prefix_width_counts[key] = prefix_width_counts.get(key, 0) + 1
    incompatibility_reasons: dict[str, int] = {}
    for row in incompatible:
        reason = row.get("failure_reason", "unspecified")
        incompatibility_reasons[reason] = incompatibility_reasons.get(reason, 0) + 1
    sampled_constant_count = sum(
        row.get("prefix_summary", {}).get("sampled_constant") is True
        for row in skeleton_compatible
    )
    flag20_entries = [row["id"] for row in rows
                      if row.get("layout", {}).get("flags", 0) & 0x20]
    entry4 = rows[4]
    entry4_sig = (entry4.get("layout", {}).get("flags"),
                  tuple(entry4.get("layout", {}).get("prefix_bases", [])),
                  tuple(entry4.get("layout", {}).get("prefix_widths", [])),
                  entry4.get("layout", {}).get("sample_stride"))
    entry4_signature_count = sum(
        (row["layout"]["flags"], tuple(row["layout"]["prefix_bases"]),
         tuple(row["layout"]["prefix_widths"]), row["layout"]["sample_stride"])
        == entry4_sig for row in skeleton_compatible
    )
    summary = {
        "table": 11, "table_entry_count": table_count,
        "rom": {"path": str(Path(rom_path)), "byte_order": byte_order,
                "normalized_sha256": normalized_sha,
                "canonical_actor_table": 5, "canonical_actor_entry": actor_entry,
                "canonical_model_id": model_id, "bone_count": bone_count},
        "counts": {"reader_compatible": len(reader_compatible),
                   f"{prefix}_skeleton_compatible": len(skeleton_compatible),
                   "reader_incompatible": len(incompatible),
                   "malformed_or_out_of_bounds": len(malformed),
                   "unknown": len(unknown)},
        "historical_runtime_ids": sorted(HISTORICAL_RUNTIME_IDS),
        "historical_runtime_ids_overlapping_reader": sorted(
            r["id"] for r in rows if r["historical_runtime_id_overlap"] and
            r["reader_compatibility"] == "READER_COMPATIBLE"),
        "static_dk_usage_verified_ids": [],
        "static_player_call_sites_are_not_dk_ownership_proof": True,
        "prefix_translation_sample_summary": {
            "sampled_constant_entries": sampled_constant_count,
            "sampled_varying_entries": len(skeleton_compatible) - sampled_constant_count,
            "distinct_prefix_width_triples": len(prefix_width_counts),
            "prefix_width_triple_counts": prefix_width_counts,
            "root_translation_scalar": "UNKNOWN_PER_ENTRY",
        },
        "reader_layout_diagnostics": {
            "header_output_count_mismatch_entries": sum(
                "header requests" in row.get("failure_reason", "")
                for row in incompatible
            ),
            "flag_0x20_unsupported_entries": len(flag20_entries),
            "flag_0x20_entry_ids": flag20_entries,
            "incompatibility_reason_counts": incompatibility_reasons,
            "entry4_exact_structural_signature_matches": entry4_signature_count,
        },
        "shortlist": shortlist,
        "actor_asset_sha256": actor_info["decompressed_sha256"],
        "entries": rows,
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--character", choices=sorted(CHARACTERS), default="dk")
    args = parser.parse_args()
    result = build_census(args.rom, args.out, args.character)
    print(json.dumps({"output": str(args.out), **result["counts"],
                      "shortlist": result["shortlist"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
