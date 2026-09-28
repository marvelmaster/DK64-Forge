"""Parse and validate the user-supplied completed-root runtime dump.

This module preserves the capture's binary32 words. Its composition helper
implements the audited DK64 direct-factor parent/root stage and requires the
per-sample Actor basis/translation now included in the basis-complete dump.
"""

from __future__ import annotations

import math
import re
import struct
from typing import Any


SAMPLE_RE = re.compile(r"^\[DK64 completed-root sample\] #(\d+) (.*)$", re.M)
FLOAT_RE = re.compile(r"(\w+)=0x([0-9A-Fa-f]{8})/([^\s]+)")
MATRIX_NAMES = ("m00", "m01", "m02", "m10", "m11", "m12",
                "m20", "m21", "m22", "t30", "t34", "t38")
ACTOR_BASIS_NAMES = ("a00", "a01", "a02", "a10", "a11", "a12",
                     "a20", "a21", "a22")


def float_from_word(word: int) -> float:
    return struct.unpack(">f", struct.pack(">I", word & 0xFFFFFFFF))[0]


def _field_map(line: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, raw, printed in FLOAT_RE.findall(line):
        word = int(raw, 16)
        value = float_from_word(word)
        out[key] = {"raw32": f"0x{word:08X}", "value": value,
                    "printed_value": printed}
    return out


def _token(line: str, key: str) -> str | None:
    match = re.search(rf"(?:^|\s){re.escape(key)}=([^\s]+)", line)
    return match.group(1) if match else None


def parse_actor_basis_line(line: str) -> dict[str, Any]:
    """Decode one new-format basis line while retaining its exact raw words."""
    prefix = "[DK64 completed-root Actor basis] "
    if not line.startswith(prefix):
        raise ValueError("Actor basis line has an unexpected prefix")
    fields = FLOAT_RE.findall(line[len(prefix):])
    if tuple(name for name, _word, _printed in fields) != ACTOR_BASIS_NAMES:
        raise ValueError("Actor basis line must contain a00..a22 in row-major order")
    raw_words: list[str] = []
    values: list[float | None] = []
    printed_values: list[str] = []
    for _name, raw, printed in fields:
        word = int(raw, 16)
        value = float_from_word(word)
        raw_words.append(f"0x{word:08X}")
        values.append(value if math.isfinite(value) else None)
        printed_values.append(printed)
    return {
        "names": list(ACTOR_BASIS_NAMES),
        "raw32": raw_words,
        "float32": values,
        "printed_values": printed_values,
        "all_finite": all(value is not None for value in values),
    }


def parse_completed_root_dump(text: str) -> dict[str, Any]:
    """Parse all sample blocks and enforce the known 16-sample capture shape."""
    status = re.search(r"^\[DK64 completed-root status\] (.*)$", text, re.M)
    if not status:
        raise ValueError("completed-root status line missing")
    status_tokens = dict(re.findall(r"(\w+)=([^\s]+)", status.group(1)))
    if status_tokens.get("captured") != "16" or status_tokens.get("error") != "none":
        raise ValueError("dump is not the expected clean 16-sample capture")

    matches = list(SAMPLE_RE.finditer(text))
    if len(matches) != 16 or [int(m.group(1)) for m in matches] != list(range(1, 17)):
        raise ValueError("expected ordered sample blocks #01 through #16")

    samples: list[dict[str, Any]] = []
    for i, match in enumerate(matches):
        block = text[match.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        lines = block.splitlines()
        sample_line = lines[0]
        header = dict(re.findall(r"(\w+)=([^\s]+)", sample_line))
        by_prefix: dict[str, list[str]] = {}
        for line in lines[1:]:
            m = re.match(r"\[DK64 completed-root ([^]]+)\] (.*)$", line)
            if m:
                by_prefix.setdefault(m.group(1), []).append(m.group(2))
        def one(prefix: str) -> str:
            rows = by_prefix.get(prefix, [])
            if len(rows) != 1:
                raise ValueError(f"sample {match.group(1)}: expected one {prefix} line")
            return rows[0]

        model_line, time_line, route_line = one("model"), one("time"), one("route")
        optional_lines = by_prefix.get("optional", [])
        if len(optional_lines) != 2:
            raise ValueError(f"sample {match.group(1)}: expected two optional-list lines")
        optional_lists = []
        for optional_line in optional_lines:
            label, _, details = optional_line.partition(" present=")
            optional_tokens = dict(re.findall(r"(\S+)=([^\s]+)", "present=" + details))
            optional_lists.append({
                "label": label,
                "present": optional_tokens.get("present") == "yes",
                "records": int(optional_tokens["records"]),
                "terminated": optional_tokens.get("terminated") == "yes",
                "targets_slot0": optional_tokens.get("targets_slot0") == "yes",
                "slot0_translation_writes": int(optional_tokens["slot0_translation_writes"]),
            })
        write_line, factors_line = one("write-order"), one("factors")
        scalar_line, actor_line = one("scalar"), one("Actor translation")
        matrix_line, translation_line = one("matrix"), one("translation")
        basis_lines = by_prefix.get("Actor basis", [])
        if len(basis_lines) > 1:
            raise ValueError(f"sample {match.group(1)}: duplicate Actor basis lines")

        model_tokens = dict(re.findall(r"(\S+)=([^\s]+)", model_line))
        time_tokens = dict(re.findall(r"(\S+)=([^\s]+)", time_line))
        route_tokens = dict(re.findall(r"(\S+)=([^\s]+)", route_line))
        write_tokens = dict(re.findall(r"(\S+)=([^\s]+)", write_line))
        adjusted = _field_map(time_line).get("adjusted")
        fraction = _field_map(time_line).get("fraction")
        if adjusted is None or fraction is None:
            raise ValueError(f"sample {match.group(1)}: adjusted time/fraction missing")
        cursor_match = re.search(r"cursors=(-?\d+)/(-?\d+)", time_line)
        if not cursor_match:
            raise ValueError(f"sample {match.group(1)}: cursor pair missing")
        f_fields = {key: {"raw64": f"0x{raw64}", **_field_map(f"{key}=0x{raw32}/{value}")[key]}
                    for key, raw64, raw32, value in re.findall(
                        r"(f2[234])=raw64=0x([0-9A-Fa-f]+) low32=0x([0-9A-Fa-f]{8})/([^\s]+)", factors_line)}
        if set(f_fields) != {"f22", "f23", "f24"}:
            raise ValueError(f"sample {match.group(1)}: incomplete f22/f23/f24 checkpoint")
        scalar = _field_map(scalar_line).get("c")
        actor_translation = _field_map(actor_line)
        matrix_fields = _field_map(matrix_line)
        if set(matrix_fields) != set(MATRIX_NAMES):
            raise ValueError(f"sample {match.group(1)}: completed matrix is not 12 components")
        if not all(math.isfinite(v["value"]) for v in matrix_fields.values()):
            raise ValueError(f"sample {match.group(1)}: completed matrix contains nonfinite values")
        if scalar is None or set(actor_translation) != {"X", "Y", "Z"}:
            raise ValueError(f"sample {match.group(1)}: scalar or Actor translation missing")
        translation_repeat = _field_map(translation_line)
        if any(translation_repeat.get(axis) != matrix_fields[matrix_axis]
               for axis, matrix_axis in (("X", "t30"), ("Y", "t34"), ("Z", "t38"))):
            raise ValueError(f"sample {match.group(1)}: translation line disagrees with matrix")

        route = header.get("route", "")
        factor_route = "alternate-unk8" if route.startswith("alternate-unk8") else (
            "direct-factors" if route.startswith("direct-factors") else None)
        if factor_route is None:
            raise ValueError(f"sample {match.group(1)}: unrecognized factor route")
        actor_basis = (parse_actor_basis_line(
            "[DK64 completed-root Actor basis] " + basis_lines[0]
        ) if basis_lines else None)
        samples.append({
            "sample": int(match.group(1)), "identity": {
                "Actor": header.get("Actor"), "AAS": header.get("AAS"),
                "state": header.get("state"), "asset": header.get("asset"),
                "logical_id": header.get("id"), "model": model_tokens.get("model"),
                "model_bone_count_u8": int(model_tokens["model+0x20_u8"], 16),
            },
            "adjusted_time": adjusted,
            "cursor0": int(cursor_match.group(1)), "cursor1": int(cursor_match.group(2)),
            "fraction": fraction,
            "route": factor_route, "route_label": route,
            "route_state": route_tokens,
            "optional_lists": optional_lists,
            "expected_slot0_write_ordinal": int(write_tokens["expected_slot0_plus38_writes"]),
            "factors": f_fields, "factor_provenance": factors_line.split(" f22=")[0].split("provenance=", 1)[-1],
            "scalar_c": scalar,
            "actor_translation": actor_translation,
            "actor_basis_words": actor_basis["raw32"] if actor_basis else None,
            "actor_basis_float32": actor_basis["float32"] if actor_basis else None,
            "actor_basis_all_finite": actor_basis["all_finite"] if actor_basis else None,
            "completed_matrix": [matrix_fields[name] for name in MATRIX_NAMES],
            "completed_translation": {k: matrix_fields[k] for k in ("t30", "t34", "t38")},
            "offline_reconstruction": None,
        })

    direct = sum(s["route"] == "direct-factors" for s in samples)
    alternate = sum(s["route"] == "alternate-unk8" for s in samples)
    if (direct, alternate) != (11, 5):
        raise ValueError(f"route counts were direct={direct}, alternate={alternate}; expected 11/5")
    if any(s["identity"]["logical_id"] != "0x0004" or
           s["identity"]["model_bone_count_u8"] != 25 or
           s["expected_slot0_write_ordinal"] != 2 for s in samples):
        raise ValueError("sample identity, model count, or expected write ordinal mismatch")
    basis_complete = all(s["actor_basis_words"] is not None for s in samples)
    variation = re.search(r"^\[DK64 completed-root variation\] (.*)$", text, re.M)
    variation_tokens = (dict(re.findall(r"(\w+)=([^\s]+)", variation.group(1)))
                        if variation else {})
    return {
        "schema": "dk64-entry4-completed-root-runtime-v1",
        "evidence": "VERIFIED RUNTIME; parsed from the supplied raw text dump",
        "capture_status": status_tokens,
        "aggregate_variation": variation_tokens,
        "sample_count": len(samples), "route_counts": {
            "direct-factors": direct, "alternate-unk8": alternate},
        "completed_matrix_component_order": list(MATRIX_NAMES),
        "samples": samples,
        "root_matrix_comparison": {
            "status": ("READY_INPUTS_CAPTURED; reconstruction not performed"
                       if basis_complete else
                       "BLOCKED: independent 12-word prediction requires the captured Actor basis"),
            "required_missing_input": (None if basis_complete else
                                       "Actor basis a00..a22 raw32 words; dump prints only Actor translation"),
            "runtime_completed_matrices_available": True,
            "word_comparisons_performed": 0,
            "word_comparisons_total": 132,
            "max_absolute_error": None,
        },
        "alternate_reconstruction": {
            "status": "PARKED",
            "sample_numbers": [1, 2, 3, 4, 5],
            "missing_state": "effective post-alternate f22/f23/f24 at root setup; early checkpoint is not authoritative on this route",
        },
        "reconstruction_limit": (None if basis_complete else
            "The dump prints Actor translation but omits the nine Actor basis raw32 words. "
            "The basis cannot be recovered as an independent input from this text."),
    }


def reconstruct_parent_root_words(
    local_words: tuple[int, ...], actor_words: tuple[int, ...],
    factor_words: tuple[int, int, int], scalar_word: int,
) -> tuple[int, ...]:
    """Assembly-ordered binary32 L0*P; Actor words use compact 3x4 order."""
    if len(local_words) != 12 or len(actor_words) != 12:
        raise ValueError("local and Actor affine matrices must contain 12 words")
    f = [float_from_word(w) for w in factor_words]
    c = float_from_word(scalar_word)
    a = [float_from_word(w) for w in actor_words]
    f32 = lambda x: float_from_word(struct.unpack(">I", struct.pack(">f", x))[0])
    u = [f32(x * c) for x in f]
    parent = list(actor_words[:9])
    for axis in range(3):
        terms = [f32(u[0] * a[axis]), f32(u[1] * a[3 + axis]),
                 f32(u[2] * a[6 + axis])]
        value = f32(f32(terms[0] + terms[1]) + terms[2])
        parent.append(struct.unpack(">I", struct.pack(">f", f32(a[9 + axis] + value)))[0])
    # code_1E2D0.s::0x8061A938..0x8061AA90 rounds each mul.s/add.s. For
    # translation specifically, it adds P.translation after the first local
    # translation product, then adds the second and third products.
    p = [float_from_word(w) for w in parent]
    l = [float_from_word(w) for w in local_words]
    def bits(value: float) -> int:
        return struct.unpack(">I", struct.pack(">f", value))[0]
    out: list[int] = []
    for row in range(3):
        for column in range(3):
            products = (
                f32(l[row * 3] * p[column]),
                f32(l[row * 3 + 1] * p[3 + column]),
                f32(l[row * 3 + 2] * p[6 + column]),
            )
            # Y column: f25 has been reused for the THIRD product when
            # A974/A9C8/AA18 add f25+f26; f27 is the SECOND product.
            # X and Z columns instead accumulate first+second, then third.
            order = (2, 0, 1) if column == 1 else (0, 1, 2)
            out.append(bits(f32(f32(products[order[0]] + products[order[1]])
                                  + products[order[2]])))
    for column in range(3):
        value = f32(l[9] * p[column])
        value = f32(value + p[9 + column])
        value = f32(value + f32(l[10] * p[3 + column]))
        value = f32(value + f32(l[11] * p[6 + column]))
        out.append(bits(value))
    return tuple(out)


def reconstruct_direct_local_roots(
    fixture: dict[str, Any], asset: bytes, bone_records: bytes,
    quarter_table: tuple[int, ...],
) -> None:
    """Attach exact captured-cursor unadjusted local bone-0 matrices in place."""
    from .pose_reconstruct import reconstruct_direct_local_pose_unadjusted
    from .animation_reader import reproduce_asset

    for sample in fixture["samples"]:
        if sample["route"] != "direct-factors":
            continue
        fraction_bits = int(sample["fraction"]["raw32"], 16)
        reader, bounds = reproduce_asset(
            asset, sample["cursor0"], sample["cursor1"], fraction_bits,
            rounding_mode="nearest_even",
        )
        if (bounds["cursor0_end"] > len(asset) or
                bounds["cursor1_end"] > len(asset)):
            raise ValueError(f"sample #{sample['sample']:02d}: reader exceeds entry-4 bounds")
        t5, t1 = struct.unpack(">75H", reader.t5), struct.unpack(">75H", reader.t1)
        poses = reconstruct_direct_local_pose_unadjusted(
            bone_records, t5, t1, quarter_table,
        )
        root = poses[0]
        if root.adjustment_count or root.pre_t5 != root.post_t5:
            raise ValueError(f"sample #{sample['sample']:02d}: unexpected local adjustment")
        words = root.matrix_words
        local_values = [float_from_word(w) for w in words]
        runtime_translation = [sample["completed_matrix"][i]["value"] for i in (9, 10, 11)]
        local_translation = local_values[9:12]
        sample["offline_reconstruction"] = {
            "status": "VERIFIED OFFLINE unadjusted Entry-4 local reconstruction",
            "captured_cursor_fraction_used": True,
            "reader_bounds": bounds,
            "reader_factor_words": [f"0x{word:08X}" for word in reader.fpr_bits],
            "reader_factor_matches": [
                word == int(sample["factors"][name]["raw32"], 16)
                for word, name in zip(reader.fpr_bits, ("f22", "f23", "f24"))
            ],
            "adjustments_applied": False,
            "bone0_local_matrix": [
                {"raw32": f"0x{word:08X}", "value": value}
                for word, value in zip(words, local_values)
            ],
            "matched_time_translation": {
                axis: {
                    "offline_model_local": local,
                    "runtime_completed": runtime,
                    "completed_minus_model_local": runtime - local,
                }
                for axis, local, runtime in zip("XYZ", local_translation, runtime_translation)
            },
        }


def _ulp_distance(left: int, right: int) -> int:
    """Distance between finite binary32 words in monotonic float order."""
    def ordered(word: int) -> int:
        word &= 0xFFFFFFFF
        return (~word & 0xFFFFFFFF) if word & 0x80000000 else (word | 0x80000000)
    return abs(ordered(left) - ordered(right))


def reconstruct_direct_completed_matrices(fixture: dict[str, Any]) -> dict[str, Any]:
    """Apply captured direct factors and Actor transform to each reconstructed L0."""
    per_sample: list[dict[str, Any]] = []
    component_matches = {name: 0 for name in MATRIX_NAMES}
    exact_words = 0
    max_abs_error = 0.0
    max_ulp = 0
    for sample in fixture["samples"]:
        if sample["route"] != "direct-factors":
            continue
        local = sample.get("offline_reconstruction")
        if not local:
            raise ValueError(f"sample #{sample['sample']:02d}: direct local matrix missing")
        basis = sample.get("actor_basis_words")
        if not basis or len(basis) != 9:
            raise ValueError(f"sample #{sample['sample']:02d}: Actor basis words missing")
        actor_translation = sample["actor_translation"]
        actor_words = tuple(int(value[2:], 16) for value in basis) + tuple(
            int(actor_translation[axis]["raw32"][2:], 16) for axis in "XYZ"
        )
        factor_words = tuple(int(sample["factors"][name]["raw32"][2:], 16)
                             for name in ("f22", "f23", "f24"))
        local_words = tuple(int(component["raw32"][2:], 16)
                            for component in local["bone0_local_matrix"])
        predicted = reconstruct_parent_root_words(
            local_words, actor_words, factor_words,
            int(sample["scalar_c"]["raw32"][2:], 16),
        )
        captured = tuple(int(component["raw32"][2:], 16)
                         for component in sample["completed_matrix"])
        matches = [a == b for a, b in zip(predicted, captured)]
        exact_words += sum(matches)
        errors = []
        ulps = []
        for name, pred_word, cap_word, match in zip(MATRIX_NAMES, predicted, captured, matches):
            if match:
                component_matches[name] += 1
            pred_value, cap_value = float_from_word(pred_word), float_from_word(cap_word)
            errors.append(abs(pred_value - cap_value))
            ulps.append(_ulp_distance(pred_word, cap_word))
        max_abs_error = max(max_abs_error, max(errors, default=0.0))
        max_ulp = max(max_ulp, max(ulps, default=0))
        per_sample.append({
            "sample": sample["sample"],
            "adjusted_time_raw32": sample["adjusted_time"]["raw32"],
            "exact_matches": sum(matches),
            "components": [
                {"name": name, "predicted_raw32": f"0x{pred:08X}",
                 "captured_raw32": f"0x{cap:08X}", "exact_match": match,
                 "absolute_error": error, "ulp_difference": ulp}
                for name, pred, cap, match, error, ulp in zip(
                    MATRIX_NAMES, predicted, captured, matches, errors, ulps)
            ],
            "predicted_matrix": [
                {"raw32": f"0x{word:08X}", "value": float_from_word(word)}
                for word in predicted
            ],
        })
    if len(per_sample) != 11:
        raise ValueError(f"expected 11 direct-factor samples, got {len(per_sample)}")
    classification = ("BIT-EXACT CLOSED" if exact_words == 132 else
                      "NOT CLOSED")
    summary = {
        "status": "computed from captured raw inputs; no fitted corrections",
        "sample_count": len(per_sample), "word_count": 132,
        "exact_word_matches": exact_words,
        "per_component_matches": component_matches,
        "max_absolute_float_error": max_abs_error,
        "max_ulp_difference": max_ulp,
        "classification": classification,
        "samples": per_sample,
    }
    fixture["direct_completed_matrix_comparison"] = summary
    fixture["root_matrix_comparison"] = {
        "status": classification,
        "required_missing_input": None,
        "runtime_completed_matrices_available": True,
        "word_comparisons_performed": 132,
        "exact_word_matches": exact_words,
        "word_comparisons_total": 132,
        "max_absolute_error": max_abs_error,
        "max_ulp_difference": max_ulp,
        "mismatches": [
            {"sample": row["sample"], "component": component["name"],
             "predicted_raw32": component["predicted_raw32"],
             "captured_raw32": component["captured_raw32"],
             "ulp_difference": component["ulp_difference"]}
            for row in per_sample for component in row["components"]
            if not component["exact_match"]
        ],
    }
    return summary
