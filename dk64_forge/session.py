"""Validated, read-only DK64 ROM session for the supported playable characters."""

from dataclasses import dataclass, field, replace
import hashlib
from pathlib import Path

from . import pipeline
from .animations import AnimationDescriptor, load_descriptors
from .characters import CHARACTERS, DK, CharacterSpec


REFERENCE_RAW_SHA256 = "5778c9ef72ef269cdcc52333710a79961a343b1f01d12189d1dbe94df3cbabed"
SUPPORTED_SHA256 = "b6347d9f1f75d38a88d829b4f80b1acf0d93344170a5fbe9546c484dae416ce3"
CHARACTER = "Donkey Kong"
MODEL_ID = 3
MODEL_ENTRY = "Actor Geometry table 5, entry 3"
ANIMATION_ENTRY = "Entry 4 — experimental idle-like animation"
STATIC_CHOICE = "None — static textured mesh"


class UnsupportedRomError(ValueError):
    """The file is not the established DK64 US revision 0 ROM."""


class RomReadError(OSError):
    """The selected file could not be read."""


class RomParseError(ValueError):
    """The supported ROM could not be reconstructed as expected."""


@dataclass(frozen=True)
class RomSource:
    path: Path
    normalized: bytes = field(repr=False)
    byte_order: str
    sha256: str
    actor: object
    mesh: object
    skeleton: object
    animation_asset: bytes
    animations: tuple[AnimationDescriptor, ...] = ()
    character: CharacterSpec = DK
    # Other characters' views of the same validated ROM, built on first use.
    _views: dict = field(default_factory=dict, compare=False, repr=False)

    def for_character(self, key: str) -> "RomSource":
        """The same ROM viewed as another supported character (cached)."""
        if key == self.character.key:
            return self
        if key not in self._views:
            view = _character_view(self.path, self.normalized, self.byte_order, self.sha256,
                                   CHARACTERS[key])
            view._views.update(self._views)
            view._views[self.character.key] = self
            self._views[key] = view
        return self._views[key]

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "character": self.character.name,
            "model_id": self.character.model_id,
            "model_entry": self.character.model_entry,
            "bones": len(self.skeleton.bones),
            "vertices": len(self.mesh.positions),
            "triangles": len(self.mesh.triangles),
            # Entry 4 is the DK-only bit-exact reference clip.
            "animation": ANIMATION_ENTRY if self.character is DK else None,
            "animation_table": 11,
            "animation_entry": 4 if self.character is DK else self.character.default_animation,
            "compatible_animation_count": len(self.animations),
        }


def load_rom(path: Path) -> RomSource:
    """Fully validate before returning a session; never write to the ROM."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RomReadError(f"Could not read ROM: {exc}") from exc
    try:
        normalized, byte_order = pipeline.static_dk.normalize_rom(raw)
    except pipeline.static_dk.ModelError as exc:
        raise UnsupportedRomError(str(exc)) from exc
    digest = hashlib.sha256(normalized).hexdigest()
    if digest != SUPPORTED_SHA256:
        raise UnsupportedRomError(
            "Unsupported ROM. DK64 US revision 0 is required "
            f"(normalized SHA-256 {digest})."
        )
    return _character_view(path, normalized, byte_order, digest, DK)


def _character_view(path: Path, normalized: bytes, byte_order: str, digest: str,
                    spec: CharacterSpec) -> RomSource:
    try:
        actor_bytes, _ = pipeline.static_dk.extract_entry(normalized, 5, spec.table5_entry)
        actor = pipeline.static_dk.parse_actor(actor_bytes)
        mesh = pipeline.static_dk.decode_actor_mesh(actor, hand_state=spec.hand_mask)
        skeleton = pipeline.dk_skeleton.parse_actor_skeleton(actor)
        if (len(skeleton.bones), len(mesh.positions), len(mesh.triangles)) != (
                spec.bones, spec.vertices, spec.triangles):
            raise ValueError(f"{spec.name} model structure differs from the verified reference")
        animation_asset = b""
        if spec is DK:
            # Entry 4 is the bit-exact DK reference clip; other characters have none.
            animation_asset, _ = pipeline.static_dk.extract_entry(normalized, 11, 4)
            animation_digest = hashlib.sha256(animation_asset).hexdigest()
            if animation_digest != pipeline.entry4_rootmotion_preview.ENTRY4_SHA256:
                raise ValueError("Entry-4 animation differs from the verified asset")
            pipeline.entry4_rootmotion_preview.statically_safe_integer_times(animation_asset)
    except Exception as exc:
        raise RomParseError(f"Supported ROM could not be parsed: {exc}") from exc
    source = RomSource(path, normalized, byte_order, digest, actor, mesh,
                       skeleton, animation_asset, character=spec)
    try:
        return replace(source, animations=load_descriptors(source))
    except Exception as exc:
        raise RomParseError(f"Supported ROM animation census could not be loaded: {exc}") from exc


class RomSession:
    """Replace the loaded ROM only after the new one validates completely."""

    def __init__(self) -> None:
        self.source: RomSource | None = None

    def load(self, path: Path) -> RomSource:
        candidate = load_rom(path)
        self.source = candidate
        return candidate
