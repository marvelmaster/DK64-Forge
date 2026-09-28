"""Ordinary interior time arithmetic from DK64 US func_global_asm_8061421C.

The caller supplies all live state. This does not model transitions, blending,
range normalization, or endpoint policy.
"""

import struct
import re

ENTRY4_DIAGNOSTIC_UNITS_PER_SECOND = 30.0


def f32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", value))[0]


def advance_interior_time(
    old_time: float,
    actor_rate: float,
    state_speed: float,
    retrace_delta: int,
    *,
    retrace_scaled: bool,
) -> float:
    """Model the three MIPS single-precision operations before 0x80614644."""
    if retrace_delta < 0:
        raise ValueError("retrace_delta must be nonnegative")
    step = f32(0.5 * retrace_delta) if retrace_scaled else f32(1.0)
    common_delta = f32(f32(actor_rate) * step)
    state_delta = f32(common_delta * f32(state_speed))
    return f32(f32(old_time) + state_delta)


def seconds_per_adjusted_unit(
    *, actor_rate: float, state_speed: float, vi_hz: float
) -> float:
    """Constant-rate retrace-scaled conversion; caller supplies the VI rate."""
    if actor_rate <= 0 or state_speed <= 0 or vi_hz <= 0:
        raise ValueError("rates must be positive")
    return 2.0 / (actor_rate * state_speed * vi_hz)


def adjusted_time_to_seconds(
    adjusted_time: float,
    *,
    units_per_second: float = ENTRY4_DIAGNOSTIC_UNITS_PER_SECOND,
) -> float:
    """Convert an adjusted sample position using an explicit display rate."""
    if units_per_second <= 0:
        raise ValueError("units_per_second must be positive")
    return float(adjusted_time) / float(units_per_second)


FLOAT_FIELDS = (
    "time", "range_start", "range_end", "state_speed", "state30", "state34",
    "actor_rate", "aas4c", "aas50", "aas54", "aas58",
)
INT_FIELDS = (
    "n", "tick", "Actor", "AAS", "state", "id", "asset", "state_flags",
    "aas30", "global_delta", "vi_count", "previous_vi", "rate_mode",
)


def parse_timing_dump(text: str) -> list[dict]:
    """Parse the probe's one-line-per-sample output; preserve float raw words."""
    records = []
    for line in text.splitlines():
        if not line.startswith("[DK64 timing sample] "):
            continue
        fields = dict(re.findall(r"([A-Za-z0-9_]+)=([^ ]+)", line))
        record = {}
        for name in INT_FIELDS:
            if name not in fields:
                raise ValueError(f"missing timing field {name}")
            record[name] = int(fields[name], 16) if fields[name].startswith("0x") else int(fields[name])
        for name in FLOAT_FIELDS:
            if name not in fields:
                raise ValueError(f"missing timing field {name}")
            raw, value = fields[name].split("/", 1)
            bits = int(raw, 16)
            decoded = struct.unpack(">f", struct.pack(">I", bits))[0]
            reported = float(value)
            if f32(reported) != decoded:
                raise ValueError(f"{name} decoded value disagrees with raw32")
            record[name + "_bits"] = bits
            record[name] = decoded
        records.append(record)
    return records


def _same_identity(a: dict, b: dict) -> bool:
    return all(a[key] == b[key] for key in ("Actor", "AAS", "state", "asset", "id"))


def validate_timing_records(records: list[dict]) -> dict:
    """Compare consecutive interior rows; update inputs are sampled post-update.

    The prior row's AAS gate was consumed by the next update; rates and global
    delta from the next row are the values visible after that update's rate
    bookkeeping and before/at the caller continuation.
    """
    transitions, mismatches = [], []
    for index, (old, new) in enumerate(zip(records, records[1:]), start=1):
        if not _same_identity(old, new) or ((old["tick"] + 1) & 0xFFFFFFFF) != new["tick"]:
            continue
        gate = bool(old["aas30"] & 1)
        predicted = advance_interior_time(
            old["time"], new["actor_rate"], new["state_speed"],
            new["global_delta"], retrace_scaled=gate,
        )
        start, end = new["range_start"], new["range_end"]
        if not (start < old["time"] < end and start < predicted < end and start < new["time"] < end):
            continue
        observed_delta = f32(new["time"] - old["time"])
        item = {
            "from_sample": index, "to_sample": index + 1,
            "old_time_bits": old["time_bits"], "new_time_bits": new["time_bits"],
            "observed_delta": observed_delta,
            "effective_step": f32(0.5 * new["global_delta"]) if gate else f32(1.0),
            "predicted_delta": f32(predicted - old["time"]),
            "predicted_time": predicted,
            "predicted_time_bits": struct.unpack(">I", struct.pack(">f", predicted))[0],
            "match": struct.pack(">f", predicted) == struct.pack(">f", new["time"]),
        }
        transitions.append(item)
        if not item["match"]:
            mismatches.append(item)
    def constant(field):
        return len({r[field + "_bits"] for r in records}) <= 1
    return {
        "records": len(records), "comparable": len(transitions),
        "exact_matches": sum(t["match"] for t in transitions),
        "mismatches": mismatches, "transitions": transitions,
        "actor_rate_constant": constant("actor_rate"),
        "state_speed_constant": constant("state_speed"),
        "global_delta_constant": len({r["global_delta"] for r in records}) <= 1,
        "gate_constant": len({r["aas30"] & 1 for r in records}) <= 1,
    }


def format_timing_validation(result: dict) -> str:
    lines = [
        "[Entry-4 timing validation] "
        f"exact={result['exact_matches']}/{result['comparable']} comparable "
        f"actor_rate_constant={str(result['actor_rate_constant']).lower()} "
        f"state_speed_constant={str(result['state_speed_constant']).lower()} "
        f"global_delta_constant={str(result['global_delta_constant']).lower()} "
        f"gate_constant={str(result['gate_constant']).lower()}"
    ]
    for item in result["transitions"]:
        lines.append(
            f"[Entry-4 timing transition] {item['from_sample']}->{item['to_sample']} "
            f"step={item['effective_step']:.9g} observed_delta={item['observed_delta']:.9g} "
            f"predicted_delta={item['predicted_delta']:.9g} "
            f"predicted_time=0x{item['predicted_time_bits']:08X} "
            f"observed_time=0x{item['new_time_bits']:08X} "
            f"match={str(item['match']).lower()}"
        )
    return "\n".join(lines)
