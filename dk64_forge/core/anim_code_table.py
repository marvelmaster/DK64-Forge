"""Table-13 animation code: which Table-11 clips each character's scripts play.

Evidence: research/DK_ANIMATION_NAMES.md.
- code_7CA80.c:52-56: Table 13 file 0 is a big-endian s32 offset table. Header
  words 1/2/3 are byte offsets of D_807FBB5C (s16), D_807FBB58 (u16 slot->clip,
  7 character columns) and D_807FBB54 (u16 playAnimation slot->script).
- code_18750.c: playAnimation(actor, slot) runs script D_807FBB54[slot*7 + kong-2];
  func_80613AF8/80613BA0/80613FB0 map a slot through D_807FBB58[slot*7 + kong-2];
  func_80613C48/80613CA8/80614014 take a Table-11 ID directly (bit 0x4000 is a flag).
- Opcode handlers: D_80746BEC in the global_asm .data overlay.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

KONG_COLUMNS = ("DK", "Diddy", "Lanky", "Tiny", "Chunky", "Krusha", "Column6")
DK_COLUMN = 0  # actor->unk58 == 2 (Model.DK playable) selects column 0
FLAG_MASK = 0x4000

# Argument byte sizes per opcode, from the getAnimationArg8/16/32 reads of each
# handler in code_18750.c (dispatch table D_80746BEC, opcodes 0x00-0x5B).
OPCODE_ARGS = {
    0x01: (2,), 0x05: (1,), 0x06: (1, 1), 0x08: (2,), 0x09: (4,), 0x0A: (4,), 0x0C: (4,),
    0x11: (4,), 0x12: (4,), 0x13: (4, 4), 0x14: (2,), 0x15: (2, 4), 0x16: (1,), 0x17: (2, 1),
    0x18: (2,), 0x19: (2, 4), 0x1A: (2, 1), 0x1B: (1,), 0x1C: (1,), 0x1D: (1, 1), 0x1E: (2,),
    0x1F: (2,), 0x20: (4,), 0x21: (4, 4), 0x24: (4, 4), 0x25: (2,), 0x26: (2,), 0x28: (2,),
    0x2A: (4,), 0x2B: (2,), 0x2C: (2,), 0x2D: (2,), 0x2E: (2,), 0x2F: (2,), 0x30: (2,),
    0x31: (2,), 0x32: (2,), 0x33: (2,), 0x36: (2,), 0x37: (2,),
    0x38: (2, 1, 1, 2, 1), 0x39: (2, 1), 0x3A: (2, 1, 1, 1, 1), 0x3B: (2, 1, 1, 1, 1, 4, 1, 1),
    0x3C: (2, 1), 0x3D: (2, 1, 2, 1), 0x3E: (2, 1, 1, 1), 0x3F: (2, 1, 1, 1, 1), 0x40: (1,),
    0x41: (2, 1, 1, 1), 0x42: (1, 4, 1), 0x43: (1, 1), 0x44: (1,), 0x45: (2, 1),
    0x46: (2, 1, 1, 1, 1), 0x48: (1,), 0x4A: (1,), 0x4C: (1, 1), 0x4F: (2,), 0x50: (2,),
    0x56: (4,), 0x58: (2,), 0x59: (1,), 0x5A: (1,),
}
NO_ARGS = {0x00, 0x02, 0x03, 0x04, 0x07, 0x0B, 0x0D, 0x0E, 0x0F, 0x10, 0x22, 0x23, 0x27,
           0x29, 0x34, 0x47, 0x49, 0x4B, 0x51, 0x52, 0x53, 0x54, 0x55, 0x57, 0x5B}
# Linear sweep stops at handlers that return 0 / restart / jump, and at the
# assembly-only handlers 0x4D/0x4E and table jump 0x35 whose lengths are not derived.
STOP = {0x00, 0x07, 0x0B, 0x27, 0x29, 0x34, 0x35, 0x4D, 0x4E, 0x54}
DIRECT_CLIP_OPS = {0x14, 0x15, 0x17, 0x4F}   # 80613C48 / 80614014 / 80613CA8
SLOT_CLIP_OPS = {0x18, 0x19, 0x1A, 0x50}     # 80613AF8 / 80613FB0 / 80613BA0


@dataclass(frozen=True)
class AnimCodeTable:
    data: bytes
    slot_clips: tuple[tuple[int, ...], ...]     # D_807FBB58 rows x 7 columns
    play_scripts: tuple[tuple[int, ...], ...]   # D_807FBB54 rows x 7 columns
    script_count: int

    def script_offset(self, script_id: int) -> int:
        if not 0 <= script_id < self.script_count:
            raise ValueError(f"script {script_id:#x} out of range")
        return struct.unpack_from(">i", self.data, script_id * 4)[0]

    def script_clips(self, script_id: int, column: int, max_ops: int = 64) -> tuple[tuple[int, str, int], ...]:
        """Linear sweep: (table11_id, route, opcode) for clip-setting opcodes."""
        pc = self.script_offset(script_id)
        found = []
        for _ in range(max_ops):
            if not 0 <= pc < len(self.data):
                break
            op = self.data[pc]
            if op not in OPCODE_ARGS and op not in NO_ARGS and op not in STOP:
                break
            values, cursor = [], pc + 1
            for size in OPCODE_ARGS.get(op, ()):
                values.append(int.from_bytes(self.data[cursor:cursor + size], "big"))
                cursor += size
            if op in DIRECT_CLIP_OPS:
                found.append((values[0] & ~FLAG_MASK & 0xFFFF, "direct", op))
            elif op in SLOT_CLIP_OPS:
                slot = values[0] & ~FLAG_MASK & 0xFFFF
                if slot < len(self.slot_clips):
                    found.append((self.slot_clips[slot][column], f"slot {slot:#04x}", op))
            if op in STOP:
                break
            pc = cursor
        return tuple(found)


def parse_anim_code(data: bytes) -> AnimCodeTable:
    header = struct.unpack_from(">4i", data, 0)
    slot_at, play_at = header[2], header[3]
    offsets = []
    for index in range(len(data) // 4):
        value = struct.unpack_from(">i", data, index * 4)[0]
        if offsets and index * 4 >= min(offsets):
            break
        offsets.append(value)
    script_count = len(offsets)
    if not (0 < slot_at < play_at < len(data)) or (play_at - slot_at) % 14:
        raise ValueError("unexpected Table-13 header layout")
    slot_rows = (play_at - slot_at) // 14
    # The playAnimation table ends where the first script body after it begins.
    play_end = min(o for o in offsets[4:] if o > play_at)
    play_rows = (play_end - play_at) // 14

    def rows(at: int, count: int):
        return tuple(struct.unpack_from(">7H", data, at + row * 14) for row in range(count))
    return AnimCodeTable(data, rows(slot_at, slot_rows), rows(play_at, play_rows), script_count)


def dk_clip_routes(table: AnimCodeTable, column: int = DK_COLUMN) -> dict[int, list[dict]]:
    """Table-11 ID -> how this character's animation code reaches it."""
    routes: dict[int, list[dict]] = {}
    for slot, row in enumerate(table.slot_clips):
        routes.setdefault(row[column], []).append({"route": "slot_table", "slot": slot})
    for play_slot, row in enumerate(table.play_scripts):
        script = row[column]
        if not 0 < script < table.script_count:
            continue
        for clip, route, op in table.script_clips(script, column):
            routes.setdefault(clip, []).append({"route": "playAnimation", "play_slot": play_slot,
                                                "script": script, "via": route, "opcode": op})
    return routes
