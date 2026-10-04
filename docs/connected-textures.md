# Connected textures

In **Textures**, select a texture and open **Connected textures** beside
**Individual texture**. Related sets are selected automatically. **Browse all
texture sets in this bank** exposes the complete catalog with live name search.

Two kinds of sets are shown:

- **Assembled image:** an established arrangement of pieces from a flat image.
  Seven original table-25 layouts are currently supported: the ship hatch/barrel
  face, Bananaport pad top, and the five Kong face portraits.
- **Packed model atlas:** images used by the same actor or prop, packed into a new
  sheet. They can include alternative animation frames. This is a convenient new
  packing, not a recovered original sheet or a flattened picture of the character.

For the supplied hatch example, **0610 is placed left of 060F**, making a 64×64
image. Map 43's shared geometry edge joins the right UV boundary of 0610 to the
left UV boundary of 060F. The ordering is established by that seam, rather than
by the numeric order of the image IDs. The Bananaport and Kong face arrangements
follow the explicit left/right mappings in DK64 Randomizer's image build and
cosmetic routines. The catalog does not guess additional flat arrangements from
neighboring texture numbers.

The supplied **Troff** example belongs to actor 38. Its atlas packs all 25 recorded,
formatted texture entries, including 0F78 and 0F80, into a 201×322 sheet with
transparent two-pixel gutters. These images cover different 3D body surfaces and
variants; they cannot be joined into one original flat Troff picture. **Open source
model / level** opens the actual textured model, where the original UV mapping
connects the pieces. The current bank-25 catalog contains 561 sets: seven flat
assemblies and 554 actor/prop atlas groups. Map-wide atlases are not generated.

## Preview and export

Mouse-wheel zoom uses nearest-neighbor scaling. The list under the preview gives
each part's ID, size and position. Double-click a part to return to that individual
texture. **Open source model / level** navigates to the first recorded source;
model atlases have one exact actor/prop source.

**Export PNG + layout…** writes the assembled pixels at their original resolution
and a JSON sidecar with the same basename. The sidecar records bank, piece IDs,
rectangles, coordinate convention and evidence. Source pixels are copied unchanged:
no resizing, color adjustment, alpha compositing or texture resampling is applied.
Flat assemblies use their documented order; atlases use stable source-ID ordering
and deterministic shelf packing. Exporting an atlas does not change the model's
UVs or make the PNG directly interchangeable with the model's original textures.
A consumer can use the JSON rectangles to build its own UV remapping.

Decoding follows the texture browser's primary usage for each image. Unknown or
conflicting model usages are not newly inferred by this feature. An undecodable
part makes a flat assembly unavailable; an atlas marks it magenta and lists the
missing image in its JSON. Completely unformatted images have no atlas rectangle.
Texture-bank changes clear the old catalog. Sessions preserve the connected tab,
selected set, browse/search state and zoom. No ROM data or exported sheets are
added to the repository.

## Validation (2026-10-04)

Seven focused tests cover exact source-pixel placement, the hatch's shared ROM UV
edge, nonoverlapping Troff packing and membership, missing-data behavior, bank
separation, source-piece/model navigation, PNG/layout export, live search and
connected-section session restoration. Existing texture-bank checks and all eight
workspace checks passed, as did the 127-test reference suite. Native Qt hatch and
Troff previews were visually inspected. Current application coverage is 185 tests
(178 previous + seven new), or 312 including the reference suite. The last full
application run is recorded in [animation-workspace.md](animation-workspace.md);
this change was validated with the relevant focused suites.
