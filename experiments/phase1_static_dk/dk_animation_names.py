"""Evidence-based labels for DK's Table-11 clips (no official names exist).

Joins the Table-13 animation code (dk_anim_code_table) with constant-argument
call sites in the DK64 decomp:
- playAnimation(actor, slot)            -> slot of D_807FBB54 (per-character script)
- func_global_asm_80613C48/80613CA8/80614014(actor, id, ...) -> direct Table-11 ID
- func_global_asm_80613AF8/80613BA0/80613FB0(actor, slot, ...) -> D_807FBB58 slot
Writes a JSON used by the app and a Markdown table for research/.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import dk_anim_code_table as anim_code
import static_dk

# Hand-checked meanings. Evidence: the DK player gait selector
# code_CEAE0.c:1179 func_806CD9A0 (speed vs. thresholds D_80753170 < D_807531A8
# < D_807531E0; tier 6 when the actor+0x6A "grounded" bit (Randomizer
# common_structs.h) is clear) and its consumer func_806CDD24:1268-1336, which
# plays the per-tier animations. Walk/Run also match the runtime-marked capture.
CURATED_LABELS = {
    0x0000: ("Idle (standing)", "LIKELY", "gait tier 0 -> playAnimation 0x09 script (code_CEAE0.c:1274)"),
    0x0004: ("Idle-like variant (standing)", "LIKELY", "same tier-0 script 0x09 alternates 0x0000/0x0004; Entry 4 visually idle-like"),
    0x0005: ("Idle variant (standing)", "LIKELY", "tier-0 script 0x09"),
    0x000A: ("Idle in shops", "LIKELY", "gait tier 2 (Funky/Cranky/Candy/Snide maps) -> playAnimation 0x87 (code_CEAE0.c:1289)"),
    0x000F: ("Idle in shops (variant)", "LIKELY", "tier-2 script 0x87"),
    0x0002: ("Slow walk", "LIKELY", "gait tier 3 -> slot 2 (code_CEAE0.c:1297-1298)"),
    0x0001: ("Walk", "LIKELY + RUNTIME ASSOCIATED", "gait tier 4 -> slot 3 (code_CEAE0.c:1311); runtime Walk capture"),
    0x0003: ("Run", "LIKELY + RUNTIME ASSOCIATED", "gait tier 5 -> slot 4 (code_CEAE0.c:1329); runtime Run capture"),
    0x0017: ("Airborne / not grounded", "LIKELY", "gait tier 6 (grounded bit clear) -> playAnimation 0x1B (code_CEAE0.c:1336)"),
    # Phase 2 state/action analysis (research/DK_ANIMATION_NAMES.md). Chains use the
    # player state table D_80750B50, input handlers D_80751004 and actions
    # action_initiation_function_list (0x80752C70), all read from the global_asm .data.
    # "?" in a label marks a meaning the static logic does not pin down fully.
    0x0007: ("Failure", "LIKELY + COMMUNITY", "action 0x43 -> state 0x73 'Failure' (Randomizer quality_fixes.c), on land (code_EFDC0.c:1224)"),
    0x0008: ("Drinking potion (Cranky)", "LIKELY", "action 0x52 spawns ACTOR_POTION, state 0x79 (code_EFDC0.c:1147)"),
    0x0009: ("Skid / turn at run speed", "LIKELY", "state 0x0E entered when speed > run threshold and stick reverses (code_D78D0.c:274-280)"),
    0x0010: ("Wall bump (head-on)", "LIKELY", "action 0x21: grounded, moving into a wall, angle < 0x200 (code_CEAE0.c:815)"),
    0x0011: ("Wall bump (glancing)", "LIKELY", "action 0x22: same wall test, angle < 0x400 (code_CEAE0.c:820)"),
    0x0013: ("Hurt / knockback", "LIKELY", "actions 0x00/0x35/0x36/0x3F -> state 0x36 after hit sound 0x2C0 (code_CEAE0.c:519-560, code_EFDC0.c:856)"),
    0x0015: ("Hurt / knockback (in water)", "LIKELY", "same hurt actions, actor+0x6A bit 4 (in water) branch (code_EFDC0.c:862)"),
    0x001D: ("Launched upward (lava / bounce)", "LIKELY", "actions 0x2C/0x2D/0x38/0x40 -> states 0x20/0x21/0x3B; 0x40 on Dogadon lava maps (code_CEAE0.c:547)"),
    0x001E: ("Bounce back (no damage?)", "LIKELY", "else-branch of hurt actions 0x35/0x36 when no damage is applied -> state 0x20 (code_EFDC0.c:912)"),
    0x0018: ("Jump from crouch (stick pushed)?", "LIKELY", "A in crouch state 0x3C with stick magnitude > 70 -> state 0x1D (code_E4090.c:1237)"),
    0x0029: ("Backflip (crouch + A)", "LIKELY", "A in crouch state 0x3C otherwise -> state 0x3E (code_E4090.c:1249)"),
    0x0028: ("Crouch (Z)", "LIKELY", "Z at low speed -> state 0x3C (code_E4090.c:1761)"),
    0x002A: ("Crouch (hold)", "LIKELY", "state 0x3C progress 1 (code_D78D0.c:1820)"),
    0x0019: ("Simian Slam", "LIKELY", "Z handler plays 0x16 for D_807FD568->simian_slam level 1 (code_E4090.c:2083)"),
    0x001F: ("Simian Slam (upgrade 2)", "LIKELY", "simian_slam == 2 -> playAnimation 0x17 (code_E4090.c:2075)"),
    0x001A: ("Simian Slam (follow-up / upgrade 3)", "LIKELY", "queued slot 0x17/0x18 after levels 1-2; simian_slam == 3 plays 0x19 (code_E4090.c:2080)"),
    0x0026: ("Pick up object", "LIKELY", "action 0x03 -> state 0x47, default branch for non-special objects (code_EFDC0.c:519)"),
    0x0022: ("Carrying object (1)?", "LIKELY", "carry gait func_806CE928 default branch (code_CEAE0.c:1629)"),
    0x0023: ("Carrying object (2)?", "LIKELY", "carry gait func_806CE928 default branch (code_CEAE0.c:1610)"),
    0x0025: ("Jump while carrying", "LIKELY", "A in carry states 0x48/0x49/0x4C (code_E4090.c:1357)"),
    0x0024: ("Throw object", "LIKELY", "B in carry states 0x48/0x49/0x4C (code_E4090.c:1739)"),
    0x0027: ("Put down object", "LIKELY", "Z in carry states 0x48/0x49/0x4C (code_E4090.c:1719)"),
    0x002C: ("Swimming underwater?", "LIKELY", "reached from state 0x4E, entered by action 0x3D with playerCanDive (code_EBBE0.c:840)"),
    0x002D: ("Dive", "LIKELY", "B at the water surface state 0x4F, playerCanDive -> action 0x3E (code_D78D0.c:1940)"),
    0x002E: ("Swimming (surface)", "LIKELY", "surface state 0x4F; failure/victory in water use play 0x34 (code_EFDC0.c:1224)"),
    0x002F: ("Swimming (surface, variant)", "LIKELY", "surface state 0x4F scripts play 0x33/0x34"),
    0x0033: ("Ledge grab", "LIKELY", "action 0x0A when falling next to a ledge -> state 0x5B (code_CEAE0.c:1996-2037)"),
    0x0034: ("Ledge climb up", "LIKELY", "ledge state 0x5B -> state 0x5C (code_D78D0.c:3618)"),
    0x0030: ("Ledge hang (variant 1)", "LIKELY", "played by ledge state 0x5B"),
    0x0031: ("Ledge hang (variant 2)", "LIKELY", "played by ledge state 0x5B"),
    0x0032: ("Ledge hang (variant 3)", "LIKELY", "played by ledge state 0x5B"),
    0x0035: ("On vine (1)", "LIKELY", "vine state 0x57 (actions 0x0B/0x0C from vine code code_133A90.c, PERMFLAG_ITEM_MOVE_VINES)"),
    0x0036: ("On vine (2)", "LIKELY", "vine state 0x57"),
    0x0037: ("On vine (3)", "LIKELY", "vine state 0x57"),
    0x0038: ("On vine (4)", "LIKELY", "vine state 0x57"),
    0x0039: ("Jump off vine", "LIKELY", "A in vine swing state 0x59 -> state 0x5A"),
    0x003A: ("Vine swing (1)", "LIKELY", "vine swing state 0x59"),
    0x003B: ("Vine swing (2)", "LIKELY", "vine swing state 0x59"),
    0x003C: ("Vine swing (3)", "LIKELY", "vine swing state 0x59"),
    0x003D: ("Vine swing (4)", "LIKELY", "vine swing state 0x59"),
    0x0041: ("Running attack (B)", "LIKELY + COMMUNITY", "B at run speed -> state 0x29 'B attack' (Randomizer instances.c; code_E4090.c:1565)"),
    0x003E: ("Attack combo, 2nd hit", "LIKELY", "B again within 0x14 frames in state 0x29 -> state 0x26 (code_D78D0.c:1188)"),
    0x003F: ("Attack combo, 2nd hit (part 2)", "LIKELY", "same combo script play 0x3F"),
    0x0040: ("Attack combo, 3rd hit", "LIKELY", "B within 0x32 frames in state 0x26 -> state 0x27/0x28 (code_D78D0.c:1052)"),
    0x0042: ("Aerial attack (B in air)", "LIKELY", "B while > 3 units above floor -> action 0x0F -> state 0x2A (code_E4090.c:1608)"),
    0x0043: ("Crouch attack (Z then B)", "LIKELY", "B in crouch state 0x3C for DK/Tiny/Krusha -> state 0x2B (code_E4090.c:1642)"),
    0x0044: ("Orange throw", "VERIFIED", "C-right with playerCanThrowOrange -> state 0x2C, decomp comment 'Throwing orange' (code_E4090.c:1177)"),
    0x0045: ("Shockwave", "LIKELY + COMMUNITY", "sets state 0x2D, Randomizer 'shockwaving' (enemies.c:90; critter/code_3340.c:313)"),
    0x0047: ("Fire gun", "LIKELY", "B only in gun-out state 0x5D (code_E4090.c:1195)"),
    0x004C: ("Jump with gun out", "LIKELY", "A in gun-out states 0x5D-0x62 -> state 0x61 (code_E4090.c:918)"),
    0x004E: ("Gun aim (Busy Barrel Barrage)", "LIKELY", "state 0x62 set on Busy Barrel Barrage maps (code_CC800.c:414)"),
    0x0049: ("Idle with gun out", "LIKELY", "gun gait func_806CE174 tier 0 plays 0x51 -> state 0x5D (code_CEAE0.c:1386)"),
    0x004A: ("Idle with gun out (variant)", "LIKELY", "same play 0x51 script"),
    0x004F: ("Idle with gun out (variant 2)", "LIKELY", "same play 0x51 script"),
    0x0051: ("Playing instrument", "LIKELY", "action 0x53 -> state 0x67, handler selects a song (code_D78D0.c:2353)"),
    0x005D: ("Warp pad teleport", "LIKELY + COMMUNITY", "Z on pad type 3 (COLLISION_MONKEYPORT_WARP, Randomizer enum) -> action 0x1D -> state 0x53 fade/noclip"),
    0x006C: ("Baboon Blast pad launch", "LIKELY + COMMUNITY", "Z on pad type 0 (COLLISION_BBLAST) -> state 0x18, launch cutscene (code_E4090.c:2005)"),
    0x006D: ("Baboon Blast flight?", "LIKELY", "state 0x45 handler flies to a target position (code_D78D0.c:2288)"),
    0x0072: ("Minecart (idle)", "LIKELY", "action 0x45 -> state 7, decomp comment 'Minecart (Idle)'"),
    0x0073: ("Minecart: A (jump?)", "LIKELY", "A in minecart states 7/9"),
    0x0074: ("Minecart: lean", "LIKELY", "writes minecart states 0xA/0xB, decomp comments 'Minecart (Left/Right)'"),
    0x0075: ("Minecart: lean (variant)", "LIKELY", "writes minecart states 0xA/0xB"),
    0x0076: ("Minecart: R", "LIKELY", "R pressed/released in minecart states 7/9"),
    0x0078: ("Minecart: Z (duck?)", "LIKELY", "Z in minecart states 7/9"),
    # Gun-out gait selector func_806CE174 (code_CEAE0.c:1386-1424): tiers 3/4/5 queue
    # slots 0x5C/0x58/0x60 and set gun-out state 0x5E.
    0x004B: ("Walk with gun out (slow)", "LIKELY", "gun gait tier 3 -> slot 0x5C (code_CEAE0.c:1398)"),
    0x0046: ("Walk with gun out", "LIKELY", "gun gait tier 4 -> slot 0x58 (code_CEAE0.c:1406)"),
    0x0048: ("Run with gun out", "LIKELY", "gun gait tier 5 -> slot 0x60 (code_CEAE0.c:1424)"),
}

# Character-specific labels by Table-13 column, for clips whose rows DK does not
# label (script lengths differ, or the move exists only for that Kong).
CHARACTER_LABELS = {
    1: {  # Diddy Kong (current_character_index 1)
        0x00A8: ("Idle (variant)", "LIKELY", "gait tier 0 script playAnimation 0x09 (code_CEAE0.c:1274)"),
        0x0116: ("Idle in shops (variant 1)", "LIKELY", "gait tier 2 script playAnimation 0x87 (code_CEAE0.c:1289)"),
        0x0117: ("Idle in shops (variant 2)", "LIKELY", "gait tier 2 script playAnimation 0x87"),
        0x0118: ("Idle in shops (variant 3)", "LIKELY", "gait tier 2 script playAnimation 0x87"),
        0x0121: ("Gun aim (Busy Barrel Barrage)", "LIKELY", "playAnimation 0x52 with state 0x62 on Busy Barrel Barrage maps (code_CC800.c:414)"),
        0x00E4: ("Bananaport warp", "LIKELY + COMMUNITY", "Z on pad type 5 (COLLISION_BANANAPORT, Randomizer enum) -> action 0x1C -> state 0x52 (code_EFDC0.c:969)"),
        0x00BA: ("Simian Spring launch", "LIKELY + COMMUNITY", "Z on pad type 2 (COLLISION_SIMIAN_SPRING) -> state 0x1B, playAnimation 0x14 (code_E4090.c:2017-2022)"),
        0x00EC: ("Chimpy Charge", "LIKELY", "B for current_character_index 1 with Diddy's move level > 0 -> state 0x2E at 4x speed, playAnimation 0x48 (code_E4090.c:1662-1668)"),
        0x00ED: ("Chimpy Charge (wall rebound)", "LIKELY", "state 0x2E handler turns 180 degrees at a wall, playAnimation 0x49 (code_D78D0.c:1311)"),
    },
}

HERE = Path(__file__).resolve().parent
DECOMP_SRC = HERE.parents[1] / "upstream" / "dk64_decomp" / "src"
DEFAULT_OUT = HERE / "local_output" / "dk_animation_names.json"

CALL = re.compile(r"\b(playAnimation|func_global_asm_806(?:13C48|13CA8|14014|13AF8|13BA0|13FB0))"
                  r"\(\s*([^,]+?)\s*,\s*(0x[0-9A-Fa-f]+|\d+)\s*[,)]")
FUNCTION = re.compile(r"^[A-Za-z_][\w \*]*?\b(\w+)\s*\([^;{]*\)\s*\{\s*$")
DIRECT = {"func_global_asm_80613C48", "func_global_asm_80613CA8", "func_global_asm_80614014"}
SLOT = {"func_global_asm_80613AF8", "func_global_asm_80613BA0", "func_global_asm_80613FB0"}
# Decomp file/overlay names that state what a code region is for.
CONTEXT_HINTS = (
    ("boss/KRool", "K. Rool fight"), ("boss/", "boss fight"), ("bonus/", "bonus barrel / minigame"),
    ("minecart/", "minecart"), ("race/", "race"), ("multiplayer/", "multiplayer"),
    ("critter/", "critter"), ("menu/", "menu"), ("arcade/", "arcade"), ("jetpac/", "jetpac"),
)


def call_sites(src: Path = DECOMP_SRC) -> list[dict]:
    sites = []
    for path in sorted(src.rglob("*.c")):
        function = None
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            match = FUNCTION.match(line)
            if match:
                function = match.group(1)
            for call in CALL.finditer(line):
                name, actor, value = call.groups()
                if name == "playAnimation" and "void playAnimation" in line:
                    continue
                kind = ("play_slot" if name == "playAnimation" else
                        "clip" if name in DIRECT else "slot")
                sites.append({"file": path.relative_to(src).as_posix(), "line": number,
                              "function": function, "call": name, "actor": actor.strip(),
                              "kind": kind, "value": int(value, 0) & ~anim_code.FLAG_MASK})
    return sites


def context_hint(file: str) -> str | None:
    return next((hint for prefix, hint in CONTEXT_HINTS if file.startswith(prefix)), None)


def build_names(table: anim_code.AnimCodeTable, sites: list[dict],
                column: int = anim_code.DK_COLUMN) -> dict[int, dict]:
    routes = anim_code.dk_clip_routes(table, column)
    by_play_slot: dict[int, list[dict]] = {}
    by_slot: dict[int, list[dict]] = {}
    by_clip: dict[int, list[dict]] = {}
    for site in sites:
        {"play_slot": by_play_slot, "slot": by_slot, "clip": by_clip}[site["kind"]].setdefault(
            site["value"], []).append(site)
    names = {}
    for clip, clip_routes in sorted(routes.items()):
        play_slots = sorted({r["play_slot"] for r in clip_routes if r["route"] == "playAnimation"})
        slots = sorted({r["slot"] for r in clip_routes if r["route"] == "slot_table"})
        evidence = ([s for p in play_slots for s in by_play_slot.get(p, [])] +
                    [s for p in slots for s in by_slot.get(p, [])] + by_clip.get(clip, []))
        hints = sorted({h for h in (context_hint(s["file"]) for s in evidence) if h})
        shared = sorted({anim_code.KONG_COLUMNS[c] for c in range(1, 5)
                         for p in slots if table.slot_clips[p][c] != clip})
        names[clip] = {
            "table11_id": clip,
            "dk_owned_static": True,
            "slots": slots, "play_slots": play_slots,
            "same_slot_other_kongs": shared,
            "call_sites": [f"{s['file']}:{s['line']} {s['function'] or '?'}" for s in evidence],
            "context_hints": hints,
            "label": CURATED_LABELS.get(clip, (None,))[0],
            "label_evidence": CURATED_LABELS[clip][1:] if clip in CURATED_LABELS else None,
        }
    return names


def labels_for(table: anim_code.AnimCodeTable, column: int) -> dict[int, tuple[str, str, str]]:
    """Labels for one character column: DK's curated labels moved along shared slots.

    Table-13 rows are per action, with one column per character, so the same
    slot row (or playAnimation row) is the same action for every Kong. A clip
    reached through several rows with different DK labels gets a '?' label.
    """
    if column == anim_code.DK_COLUMN:
        return dict(CURATED_LABELS)
    candidates: dict[int, dict[str, list[str]]] = {}

    def add(clip: int, dk_clip: int, row: str) -> None:
        label = CURATED_LABELS[dk_clip][0].rstrip("?")
        candidates.setdefault(clip, {}).setdefault(label, []).append(f"{row} (DK {dk_clip:04X})")

    for slot, row in enumerate(table.slot_clips):
        if row[anim_code.DK_COLUMN] in CURATED_LABELS:
            add(row[column], row[anim_code.DK_COLUMN], f"slot {slot:02X}")
    for play_slot, row in enumerate(table.play_scripts):
        dk_script, own_script = row[anim_code.DK_COLUMN], row[column]
        if not (0 < dk_script < table.script_count and 0 < own_script < table.script_count):
            continue
        dk_clips = [clip for clip, _route, _op in table.script_clips(dk_script, anim_code.DK_COLUMN)]
        own_clips = [clip for clip, _route, _op in table.script_clips(own_script, column)]
        # Only positionally aligned scripts transfer; differing scripts stay unlabelled.
        if len(dk_clips) == len(own_clips):
            for dk_clip, clip in zip(dk_clips, own_clips):
                if dk_clip in CURATED_LABELS:
                    add(clip, dk_clip, f"play {play_slot:02X}")
    labels = {}
    for clip, options in candidates.items():
        chain = "; ".join(route for routes in options.values() for route in routes[:2])
        if len(options) == 1:
            (label,) = options
            labels[clip] = (label, "LIKELY (Table-13 row shared with DK)", chain)
        else:
            labels[clip] = (" / ".join(sorted(options)) + "?",
                            "LIKELY? (rows shared with differently labelled DK clips)", chain)
    labels.update(CHARACTER_LABELS.get(column, {}))
    return labels


def short_label(entry: dict) -> str:
    """Compact selector text: slot identity plus any decomp context hint."""
    parts = []
    if entry["slots"]:
        parts.append("slot " + "/".join(f"{s:02X}" for s in entry["slots"][:2]))
    if entry["play_slots"]:
        parts.append("play " + "/".join(f"{s:02X}" for s in entry["play_slots"][:2]))
    if entry["context_hints"]:
        parts.append(", ".join(entry["context_hints"][:2]))
    return " · ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    rom, _ = static_dk.identify_rom(args.rom)
    table = anim_code.parse_anim_code(static_dk.extract_entry(rom, table_id=13, index=0)[0])
    names = build_names(table, call_sites())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"column": "DK", "clips": {f"{k:#06x}": v for k, v in names.items()}},
                                   indent=2) + "\n", encoding="utf-8")
    print(f"{len(names)} DK clips -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
