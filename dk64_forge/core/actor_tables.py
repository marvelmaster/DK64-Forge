"""Which actor model the game spawns for a level's setup actors and character spawners.

Both tables are read from the ROM's global_asm .data (``control_states.global_asm_data``).
Model numbers are 1-based (table-5 entry + 1), as in ``spawnActor(type, model)``.

Setup actors (pointer table 9, 0x38-byte rows): ``func_global_asm_80688FC0`` passes each
row to ``func_global_asm_80689250`` (decomp code_8D3E0.c) with behaviour ``row[0x32]``,
position ``row[0..0xC]``, y rotation ``row[0x30]`` and scale ``row[0xC]``. The behaviour
+ 0x10 is looked up in ``D_global_asm_8074E8B0`` (128 rows of 0x30 bytes: actor type u16,
model u16, ..., debug name at 0x14); the actor spawns with that model and scale
``row scale * 0.15`` (the 0.15 is ``scaleFactor`` in the same file). Table layout facts also
appear in DK64 Randomizer ``base-hack/build/getDefaultData.py`` (MIT).

Character spawners (pointer table 16): ``spawnActor(D_global_asm_8075EB80[v].unk0,
D_global_asm_8075EB80[v].unk2)`` with ``v`` the row's enemy value (decomp code_1295B0.c,
0x18-byte rows; v ranges 0..112 in the ROM). Fungi Forest swaps three enemy values at night
(``func_global_asm_807278C0``); Forge shows the day spawn. The spawned actor's scale is
``byte 0xF * 5 / 255 * 0.15`` (``func_global_asm_80726744``, read from the ROM's MIPS code:
it multiplies the byte by 5, divides by the double 255.0 at 0x8075F6C0 and multiplies by
the double 0.15 at 0x8075F6C8; a zero byte keeps the default).

Every actor model starts at scale 0.15 (``func_global_asm_806134B4``, code_17B90.c); actor
scripts may change scale later, which Forge does not simulate.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

from .control_states import GLOBAL_ASM_DATA_VRAM, global_asm_data

SETUP_DEFS_VRAM, SETUP_DEFS_COUNT, SETUP_DEFS_STRIDE = 0x8074E8B0, 128, 0x30
ENEMY_DEFS_VRAM, ENEMY_DEFS_COUNT, ENEMY_DEFS_STRIDE = 0x8075EB80, 113, 0x18
ACTOR_MODEL_COUNT = 236
DEFAULT_ACTOR_SCALE = 0.15
ANGLE_UNITS = 4096  # DK64 12-bit angles


@dataclass(frozen=True)
class ActorDef:
    actor_type: int
    model: int        # 1-based model number, 0 = none
    name: str = ""    # debug name (setup table only)

    @property
    def table5_entry(self) -> int | None:
        return self.model - 1 if 0 < self.model <= ACTOR_MODEL_COUNT else None


@dataclass(frozen=True)
class ActorTables:
    setup: tuple[ActorDef, ...]
    enemies: tuple[ActorDef, ...]

    def setup_def(self, actor_type: int) -> ActorDef | None:
        """First definition for a setup actor's type (behaviour + 0x10), as the game searches."""
        return next((d for d in self.setup if d.actor_type == actor_type), None)

    def enemy_def(self, enemy_value: int) -> ActorDef | None:
        return self.enemies[enemy_value] if 0 <= enemy_value < len(self.enemies) else None


def load_actor_tables(rom: bytes) -> ActorTables:
    data = global_asm_data(rom)

    def rows(vram, count, stride):
        at = vram - GLOBAL_ASM_DATA_VRAM
        if at < 0 or at + count * stride > len(data):
            raise ValueError("actor definition table outside global_asm .data")
        return [data[at + i * stride:at + (i + 1) * stride] for i in range(count)]

    setup = []
    for row in rows(SETUP_DEFS_VRAM, SETUP_DEFS_COUNT, SETUP_DEFS_STRIDE):
        actor_type, model = struct.unpack_from(">HH", row)
        name = row[0x14:0x30].split(b"\0", 1)[0].decode("ascii", "replace")
        setup.append(ActorDef(actor_type, model, name))
    enemies = [ActorDef(*struct.unpack_from(">HH", row))
               for row in rows(ENEMY_DEFS_VRAM, ENEMY_DEFS_COUNT, ENEMY_DEFS_STRIDE)]
    return ActorTables(tuple(setup), tuple(enemies))


def spawner_scale(scale_byte: int) -> float:
    """World scale of a character spawner's actor (func_global_asm_80726744)."""
    return scale_byte * 5 / 255.0 * DEFAULT_ACTOR_SCALE if scale_byte else DEFAULT_ACTOR_SCALE


def setup_actor_scale(row_scale: float) -> float:
    """World scale of a setup actor (code_8D3E0.c: spawner scale * 0.15)."""
    return row_scale * DEFAULT_ACTOR_SCALE
