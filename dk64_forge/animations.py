"""Evidence-aware Table-11 browser descriptors and bounded offline poses.

Only Entry 4 has a runtime-validated root scalar and 30 Hz unit-rate interval.
Other compatible entries use those numerical choices solely as an explicitly
labelled browser diagnostic, never as a DK64 runtime claim.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import struct

from . import pipeline
from .characters import DK, CharacterSpec


CENSUS_PATH = (Path(__file__).resolve().parents[1] / "local_output" /
               "dk_table11_animation_census.json")
# Runtime-observed on the identity-locked DK Actor/AAS capture.
DK_OWNERSHIP_RUNTIME_IDS = frozenset((0, 1, 2, 3, 4, 0x17))
DK_OWNED_IDS = DK_OWNERSHIP_RUNTIME_IDS  # historical name
DIAGNOSTIC_UNITS_PER_SECOND = 30.0
GENERIC_TIMING_BASIS = "diagnostic browser rate; unverified for this entry"
GENERIC_SCALAR_BASIS = "Entry-4 direct scalar reused for diagnostic preview; unverified for this entry"


@dataclass(frozen=True)
class AnimationDescriptor:
    table11_id: int
    label: str
    ownership: str
    semantic_evidence: str
    compressed_size: int
    decompressed_size: int
    decompressed_sha256: str
    marker: int
    stride: int
    prefix_bases: tuple[int, int, int]
    prefix_widths: tuple[int, int, int]
    flags: int
    safe_first: int
    safe_last: int
    structural_compatibility: str
    timing_basis: str
    prefix_scalar_basis: str
    # Table-13 animation code (dk_anim_code_table): DK column slots / playAnimation slots.
    dk_slots: tuple[int, ...] = ()       # Table-13 slots in this character's column
    dk_play_slots: tuple[int, ...] = ()
    character: str = "dk"
    source_scripts: tuple[int, ...] = ()

    @property
    def owned(self) -> bool:
        """Played by character-specific Table-13/source calls (or runtime-locked)."""
        return "_OWNERSHIP_VERIFIED" in self.ownership

    @property
    def dk_owned(self) -> bool:  # historical name used by the DK-only UI/tests
        return self.owned

    @property
    def sample_count(self) -> int:
        return self.safe_last - self.safe_first + 1

    @property
    def seconds_per_unit(self) -> float:
        return 1.0 / DIAGNOSTIC_UNITS_PER_SECOND


def descriptor_sort_key(descriptor):
    """Owned, semantically labelled clips first; retain IDs as a stable tiebreaker."""
    named = descriptor.semantic_evidence not in ("", "unknown") and not descriptor.label.startswith("Unassigned")
    name = descriptor.label.partition("—")[2].strip() or descriptor.label.rsplit("·",1)[0].strip()
    return not descriptor.owned, not named, name.casefold(), descriptor.table11_id


def _anim_code(source):
    data, _ = pipeline.static_dk.extract_entry(source.normalized, table_id=13, index=0)
    return pipeline.dk_anim_code_table.parse_anim_code(data)


def _dk_routes(source, column: int = 0) -> dict[int, list[dict]]:
    """Table-11 IDs that a character's Table-13 animation code plays, with their slots."""
    return pipeline.dk_anim_code_table.character_clip_routes(_anim_code(source), column)


def _route_suffix(slots, play_slots) -> str:
    parts = []
    if slots:
        parts.append("slot " + "/".join(f"{s:02X}" for s in slots[:2]))
    if play_slots:
        parts.append("play " + "/".join(f"{s:02X}" for s in play_slots[:2]))
    return " · ".join(parts)


def descriptor_from_row(row: dict, routes: dict[int, list[dict]] | None = None,
                        character: CharacterSpec = DK,
                        labels: dict[int, tuple[str, str, str]] | None = None) -> AnimationDescriptor:
    compat_field = f"{character.census_prefix}_skeleton_compatibility"
    if (row.get("reader_compatibility") != "READER_COMPATIBLE" or
            row.get(compat_field) != character.compatibility_value):
        raise ValueError("Table-11 entry is outside the compatible browser set")
    is_dk = character.is_dk
    animation_id = int(row["id"])
    layout = row["layout"]
    marker = int(layout["endpoint_like_marker"])
    if marker < 3 or marker > 255:
        raise ValueError(f"entry {animation_id}: unsupported interior marker {marker}")
    bases = tuple(int(x) for x in layout["prefix_bases"])
    widths = tuple(int(x) for x in layout["prefix_widths"])
    if (len(bases) != 3 or len(widths) != 3 or
            int(layout["descriptor_output_count"]) != character.channels):
        raise ValueError(f"entry {animation_id}: invalid compatible layout")
    runtime_owned = is_dk and animation_id in DK_OWNERSHIP_RUNTIME_IDS
    reference = is_dk and animation_id == 4  # Entry 4: bit-exact DK reference clip
    clip_routes = (routes or {}).get(animation_id, [])
    static_owned = bool(clip_routes)
    table_owned = any(not r["route"].startswith("source_") for r in clip_routes)
    source_scripts = tuple(sorted({r["script"] for r in clip_routes if r["route"] == "source_script"}))
    slots = tuple(sorted({r["slot"] for r in clip_routes if r["route"] == "slot_table"}))
    play_slots = tuple(sorted({r["play_slot"] for r in clip_routes if r["route"] == "playAnimation"}))
    label_table = pipeline.dk_animation_names.CURATED_LABELS if labels is None else labels
    curated = (label_table.get(animation_id)
               if (static_owned or runtime_owned or routes is None) else None)
    suffix = _route_suffix(slots, play_slots)
    if not suffix and source_scripts:
        suffix = "script " + "/".join(f"{s:03X}" for s in source_scripts)
    short = "DK" if is_dk else character.name.split()[0]
    owner = character.key.upper()
    if curated:
        label = f"{animation_id:04X} — {curated[0]}"
    elif static_owned:
        label = f"{animation_id:04X} — {short} · {suffix}"
    elif runtime_owned:
        label = f"{animation_id:04X} — DK runtime observed"
    else:
        label = f"{animation_id:04X} — not in {short} animation code"
    ownership = (f"{owner}_OWNERSHIP_VERIFIED_STATIC_TABLE13+RUNTIME" if static_owned and runtime_owned
                 else f"{owner}_OWNERSHIP_VERIFIED_STATIC_TABLE13" if table_owned
                 else f"{owner}_OWNERSHIP_VERIFIED_STATIC_SOURCE" if static_owned
                 else "DK_OWNERSHIP_VERIFIED_RUNTIME" if runtime_owned else "OWNERSHIP_UNKNOWN")
    return AnimationDescriptor(
        animation_id, label, ownership,
        (f"{curated[1]}: {curated[2]}; not an official name" if curated else "unknown"),
        int(row["compressed_size"]), int(row["decompressed_size"]),
        str(row["decompressed_sha256"]), marker, int(layout["sample_stride"]),
        bases, widths, int(layout["flags"]), 0, marker - 2,
        character.compatibility_value,
        ("live-validated Entry-4 unit-rate interval" if reference
         else GENERIC_TIMING_BASIS),
        ("verified direct Entry-4 scalar 0x38000001" if reference
         else GENERIC_SCALAR_BASIS),
        slots, play_slots, character.key, source_scripts,
    )


def load_descriptors(source, census_path: Path | None = None) -> tuple[AnimationDescriptor, ...]:
    character = getattr(source, "character", DK)
    path = Path(census_path or (CENSUS_PATH if character.is_dk else character.census_path))
    if not path.is_file():
        pipeline.dk_table11_animation_census.build_census(source.path, path, character.key)
    census = json.loads(path.read_text(encoding="utf-8"))
    expected = character.compatible_clips
    counts = census.get("counts", {})
    if (census.get("rom", {}).get("normalized_sha256") != source.sha256 or
            counts.get(f"{character.census_prefix}_skeleton_compatible") != expected or
            census.get("table_entry_count") != 1731):
        raise ValueError("Table-11 census does not match the loaded supported ROM")
    compat_field = f"{character.census_prefix}_skeleton_compatibility"
    rows = [row for row in census["entries"]
            if row.get(compat_field) == character.compatibility_value]
    if len(rows) != expected:
        raise ValueError("compatible Table-11 census count/IDs changed")
    # Single-pose entries (marker 2, one reader pair) cannot be played as a clip;
    # Diddy has one (0x0588). They stay in the census but not in the browser.
    rows = [row for row in rows if int(row["layout"]["endpoint_like_marker"]) >= 3]
    table = _anim_code(source)
    routes = pipeline.dk_anim_code_table.character_clip_routes(table, character.table13_column)
    labels = pipeline.dk_animation_names.labels_for(table, character.table13_column)
    descriptors = tuple(descriptor_from_row(row, routes, character, labels)
                        for row in sorted(rows, key=lambda r: int(r["id"])))
    if len({d.table11_id for d in descriptors}) != len(rows):
        raise ValueError("compatible Table-11 census count/IDs changed")
    return descriptors


def descriptor_for(source, animation_id: int) -> AnimationDescriptor:
    for descriptor in source.animations:
        if descriptor.table11_id == animation_id:
            return descriptor
    raise ValueError(f"Table-11 entry {animation_id} is not in the compatible browser set")


def extract_compatible_asset(source, descriptor: AnimationDescriptor) -> bytes:
    asset, _info = pipeline.static_dk.extract_entry(source.normalized, 11, descriptor.table11_id)
    if (len(asset) != descriptor.decompressed_size or
            hashlib.sha256(asset).hexdigest() != descriptor.decompressed_sha256):
        raise ValueError(f"entry {descriptor.table11_id}: asset differs from the validated census")
    if (asset[0x12] != descriptor.marker or asset[0x13] != descriptor.stride or
            int.from_bytes(asset[4:6], "big") != descriptor.flags or
            tuple(int.from_bytes(asset[8 + i*2:10 + i*2], "big", signed=True)
                  for i in range(3)) != descriptor.prefix_bases or
            tuple(asset[0x0E:0x11]) != descriptor.prefix_widths):
        raise ValueError(f"entry {descriptor.table11_id}: header disagrees with census")
    return asset


def sample_compatible_animation(source, descriptor: AnimationDescriptor, *, procedural_hair=False):
    """Reuse the established reader/local builder and origin-centred root math."""
    asset = extract_compatible_asset(source, descriptor)
    bones = len(source.skeleton.bones)
    channels = 3 * max(b.master_index for b in source.skeleton.bones) + 3
    if descriptor.table11_id == 4 and descriptor.character == "dk":
        times = pipeline.entry4_rootmotion_preview.statically_safe_integer_times(asset)
        records = source.actor.data[source.actor.bone_start:source.actor.bone_start + 25 * 16]
        quarter = pipeline.trace_one_bone.quarter_table_words_from_rom(source.normalized)
        return pipeline.entry4_rootmotion_preview.sample_interior_sequence(
            asset, records, quarter, times), records

    preview = pipeline.entry4_rootmotion_preview
    records = source.actor.data[source.actor.bone_start:source.actor.bone_start + bones * 16]
    quarter = pipeline.trace_one_bone.quarter_table_words_from_rom(source.normalized)
    samples = []
    hair = None
    if procedural_hair and source.character.key == "tiny":
        from .core.tiny_hair import TinyHair
        hair = TinyHair(source.normalized, descriptor.table11_id)
    for cursor0 in range(descriptor.safe_first, descriptor.safe_last + 1):
        cursor1 = cursor0 + 1
        try:
            reader, bounds = pipeline.reproduce_unk0_reader.reproduce_asset(
                asset, cursor0, cursor1, 0, rounding_mode="nearest_even")
            if len(reader.t5) != 2 * channels or len(reader.t1) != 2 * channels:
                raise ValueError(f"reader output is not {channels} t5/t1 words")
            t5 = struct.unpack(f">{channels}H", reader.t5)
            t1 = struct.unpack(f">{channels}H", reader.t1)
            locals_ = pipeline.reconstruct_direct_pose.reconstruct_direct_local_pose_unadjusted(
                records, t5, t1, quarter, expected_bones=bones)
            if hair is not None:
                # A reproducible offline diagnostic: use the head's clip-space Y
                # for both anchors; world heading/speed are zero. Game collision
                # anchors 13/14 and Actor movement require a runtime capture.
                composed_anchor = pipeline.compose_direct_pose.compose_direct_hierarchy(
                    locals_, pipeline.compose_direct_pose.identity_root_words())
                head_words = composed_anchor[2].matrix_words
                head_y = struct.unpack(">f", struct.pack(">I", head_words[10]))[0]
                adjustments = hair.step((head_y, head_y))
                locals_ = pipeline.reconstruct_direct_pose.reconstruct_direct_local_pose(
                    records, t5, t1, adjustments, quarter, expected_bones=bones)
            if len(locals_) != bones or not hair and any(b.adjustment_count or b.pre_t5 != b.post_t5
                                            for b in locals_):
                raise ValueError(f"local pose does not have {bones} unadjusted bones")
            root = preview.origin_centered_root_words(locals_[0].matrix_words,
                                                       tuple(reader.fpr_bits))
            locals_ = tuple(replace(b, matrix_words=root) if i == 0 else b
                            for i, b in enumerate(locals_))
            composed = pipeline.compose_direct_pose.compose_direct_hierarchy(
                locals_, pipeline.compose_direct_pose.identity_root_words())
            local_words = tuple(b.matrix_words for b in locals_)
            composed_words = tuple(b.matrix_words for b in composed)
            preview._assert_finite_matrices(local_words, bones)
            preview._assert_finite_matrices(composed_words, bones)
            samples.append(preview.Entry4Sample(float(cursor0), cursor0, cursor1, 0,
                                                local_words, composed_words, bounds,
                                                tuple(reader.fpr_bits)))
        except Exception as exc:
            raise ValueError(f"entry {descriptor.table11_id:04X} sample {cursor0}: {exc}") from exc
    if len(samples) != descriptor.sample_count or not all(math.isfinite(s.adjusted_time)
                                                           for s in samples):
        raise ValueError(f"entry {descriptor.table11_id}: incomplete safe sample interval")
    return tuple(samples), records
