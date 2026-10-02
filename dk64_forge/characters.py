"""Supported playable characters and their verified model/animation parameters.

Evidence: research/DIDDY_KONG.md (Diddy) and research/PHASE1_STATIC_DK.md (DK).
- Model ids / table-5 entries: DK64 Randomizer randomizer/Enums/Models.py
  (Diddy = 0, DK = 3) and ROM parsing.
- Hand masks: decomp code_F56F0.c func_806F09F0 per actor type
  (ACTOR_DK 0x2 sets bit 0 -> 1; ACTOR_DIDDY 0x3 clears bits 0-2 -> 0).
- Table-13 column: actor->unk58 - 2 (code_18750.c playAnimation).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


LOCAL_OUTPUT = Path(__file__).resolve().parents[1] / "local_output"


@dataclass(frozen=True)
class CharacterSpec:
    key: str
    name: str
    model_id: int
    table5_entry: int
    actor_type: int
    table13_column: int
    hand_mask: int
    bones: int
    vertices: int
    triangles: int
    stored_uv_triangles: int
    texgen_triangles: int
    compatible_clips: int
    default_animation: int
    variant: str = "normal"

    @property
    def is_dk(self) -> bool:
        """DK (any model variant): Entry-4 reference clip and the legacy census path."""
        return self.key == "dk"

    @property
    def display_name(self) -> str:
        return self.name if self.variant == "normal" else f"{self.name} ({self.variant})"

    @property
    def model_entry(self) -> str:
        return f"Actor Geometry table 5, entry {self.table5_entry}"

    @property
    def census_path(self) -> Path:
        return LOCAL_OUTPUT / f"{self.key}_table11_animation_census.json"

    @property
    def census_prefix(self) -> str:
        return self.key

    @property
    def compatibility_value(self) -> str:
        return f"{self.key.upper()}_SKELETON_STRUCTURALLY_COMPATIBLE"

    @property
    def channels(self) -> int:
        return 3 * self.bones


DK = CharacterSpec("dk", "Donkey Kong", 3, 3, 0x2, 0, 1, 25, 781, 704, 46, 658, 183, 4)
DIDDY = CharacterSpec("diddy", "Diddy Kong", 0, 0, 0x3, 1, 0, 37, 653, 678, 58, 620, 176, 0xA2)
TINY = CharacterSpec("tiny", "Tiny Kong", 8, 8, 0x5, 3, 1, 39, 822, 744, 96, 648, 165, 0x1D0)
CHUNKY = CharacterSpec("chunky", "Chunky Kong", 11, 11, 0x6, 4, 1, 23, 692, 699, 70, 629, 184, 0x262)
LANKY = CharacterSpec("lanky", "Lanky Kong", 5, 5, 0x4, 2, 1, 21, 776, 704, 95, 609, 171, 0x13E)
CHARACTERS = {spec.key: spec for spec in (DK, DIDDY, TINY, CHUNKY, LANKY)}


# --- Model variants: weapon drawn and instrument models -------------------------------
# Weapons are part of each Kong model: conditional display-list blocks selected by
# actor->unk146 bits (rom_model.decode_actor_mesh hand_state). The game sets them when
# the weapon is drawn in func_global_asm_806F0D68 and back in func_global_asm_806F0C18
# (decomp code_F56F0.c, keyed by the model number = table-5 entry + 1): DK, Lanky, Tiny
# and Chunky clear bit 0 and set bit 1 (mask 2); Diddy sets both (mask 3, two pistols).
# Instrument models are separate table-5 entries with the same skeleton (DK64 Randomizer
# Models.py "... With Instrument"); 806F0C18 gives them the Kong's normal hand mask.
# DK's bongos are a separate 3-bone actor (table 5 entry 0xA5), not a Kong variant.
VARIANT_NORMAL, VARIANT_WEAPON, VARIANT_INSTRUMENT = "normal", "weapon", "instrument"
WEAPON_MASKS = {"dk": 2, "diddy": 3, "lanky": 2, "tiny": 2, "chunky": 2}
INSTRUMENT_ENTRIES = {"diddy": 1, "lanky": 6, "tiny": 9, "chunky": 12}
# Item names from the game's manual/community usage (labels, COMMUNITY).
WEAPON_NAMES = {"dk": "Coconut Shooter", "diddy": "Peanut Popguns", "lanky": "Grape Shooter",
                "tiny": "Feather Bow", "chunky": "Pineapple Launcher"}
INSTRUMENT_NAMES = {"diddy": "Guitar", "lanky": "Trombone", "tiny": "Saxophone", "chunky": "Triangle"}


def variants_for(spec: CharacterSpec) -> list[tuple[str, str]]:
    """(variant, label) pairs the character supports, normal first."""
    rows = [(VARIANT_NORMAL, "Normal"), (VARIANT_WEAPON, f"Weapon drawn ({WEAPON_NAMES[spec.key]})")]
    if spec.key in INSTRUMENT_ENTRIES:
        rows.append((VARIANT_INSTRUMENT, f"With instrument ({INSTRUMENT_NAMES[spec.key]})"))
    return rows


def variant_model(spec: CharacterSpec, variant: str) -> tuple[int, int]:
    """(table-5 entry, hand mask) of a character variant."""
    if variant == VARIANT_WEAPON:
        return spec.table5_entry, WEAPON_MASKS[spec.key]
    if variant == VARIANT_INSTRUMENT:
        if spec.key not in INSTRUMENT_ENTRIES:
            raise ValueError(f"{spec.name} has no instrument model variant")
        return INSTRUMENT_ENTRIES[spec.key], spec.hand_mask
    return spec.table5_entry, spec.hand_mask


def variant_spec(spec: CharacterSpec, variant: str, mesh) -> CharacterSpec:
    """The character's spec for a model variant, with the topology of its decoded mesh."""
    from dataclasses import replace
    entry, mask = variant_model(spec, variant)
    texgen = sum(1 for mode in mesh.triangle_texgen_modes if mode[0])
    return replace(spec, table5_entry=entry, hand_mask=mask, variant=variant,
                   vertices=len(mesh.positions), triangles=len(mesh.triangles),
                   texgen_triangles=texgen, stored_uv_triangles=len(mesh.triangles) - texgen)
