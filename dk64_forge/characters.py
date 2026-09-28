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


LOCAL_OUTPUT = (Path(__file__).resolve().parents[1] / "experiments" /
                "phase1_static_dk" / "local_output")


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
