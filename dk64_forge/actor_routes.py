"""Source-confirmed actor scripts, resolved through the ROM Table-13 bytecode.

Model IDs are zero-based Table-5 indices. A compatible skeleton is not evidence
of ownership. Only direct clip opcodes are accepted for these non-Kong actors.
"""
from .core import anim_code_table, texture_bank

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

def confirmed_routes(rom, entry):
    if entry not in SCRIPTS:
        return {}
    table = anim_code_table.parse_anim_code(texture_bank.table_entry(rom, 13, 0))
    result = {}
    for script, label, evidence in SCRIPTS[entry]:
        for clip, route, opcode in table.script_clips(script, 0):
            if route == "direct":
                result.setdefault(clip, (label, f"{evidence}; script {script:03X}, opcode {opcode:02X}"))
    return result
