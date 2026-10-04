"""Source-confirmed actor scripts, resolved through the ROM Table-13 bytecode.

Model IDs are zero-based Table-5 indices. A compatible skeleton is not evidence
of ownership. Only direct clip opcodes are accepted for these non-Kong actors.
"""
from .core import anim_code_table, texture_bank
from functools import lru_cache

# Dispatch 8074C0A0 + enemy model table 8075EB80; CC0 dk64_decomp.
SCRIPTS = {
    0x19: ((0x1F8, "Beaver movement", "806AD54C / code_B1F60.c"),),
    0x1B: ((0x250, "Zinger flight", "806B486C -> 806B42A8 / code_B7490.c"),),
    0x20: ((0x232, "Klaptrap movement", "806B75F4 -> 806B6DB0 / code_BB300.c"),),
    0x23: ((0x237, "Skeleton Klaptrap", "806B6C88 / code_BB300.c"),),
    # Enemy-model/dispatch tables and the initializer's MIPS argument constants.
    # Klump's actual initializer uses 2AD/2AB/2AC, not the nearby Kop handler.
    0x30: ((0x1FB, "Kremling movement", "806AE588; ROM call 806AE5BC -> 8072B79C"),),
    0x39: ((0x2AB, "Klump movement", "806AEE84; ROM call 806AEEF4 -> 8072B79C"),),
    0x41: ((0x2D7, "Krossbones movement", "806AFB58; ROM call 806AFB90 -> 8072B79C"),),
    0x45: ((0x2F4, "Spider movement", "806AD9F4; ROM call 806ADA38 -> 8072B79C"),),
    0x60: ((0x35F, "Kosha movement", "806B0848; ROM call 806B0B14 -> 8072B79C"),),
}

@lru_cache(maxsize=512)
def confirmed_routes(rom, entry, curated_only=False):
    table = anim_code_table.parse_anim_code(texture_bank.table_entry(rom, 13, 0))
    from .actor_route_catalog import CALLS
    result = {}
    for kind, value, label, evidence in (() if curated_only else CALLS.get(entry, ())):
        clips = ((value, "direct", None),) if kind == "clip" else table.script_clips(value, 0)
        for clip, route, opcode in clips:
            if route == "direct":
                detail = f"{kind} {value:03X}"
                result.setdefault(clip, (f"{label} ({detail})", f"{evidence}; {detail}"))
    # Curated semantic names take priority over generic script-context labels.
    for script, label, evidence in SCRIPTS.get(entry, ()):
        for clip, route, opcode in table.script_clips(script, 0):
            if route == "direct":
                result[clip] = (label, f"{evidence}; script {script:03X}, opcode {opcode:02X}")
    return result


@lru_cache(maxsize=2)
def known_clip_names(rom):
    """Cross-model names are browsing hints, never ownership of the current model."""
    from .actor_route_catalog import CALLS
    from .core.animation_labels import labels_for
    from .characters import CHARACTERS
    result = {}
    table = anim_code_table.parse_anim_code(texture_bank.table_entry(rom, 13, 0))
    for character in CHARACTERS.values():
        for clip, (name, confidence, evidence) in labels_for(table, character.table13_column).items():
            result.setdefault(clip, (f"{character.name}: {name}", f"{confidence}; {evidence}"))
    for entry in sorted(set(CALLS) | set(SCRIPTS)):
        for clip, detail in confirmed_routes(rom, entry).items():
            result.setdefault(clip, detail)
    return result
