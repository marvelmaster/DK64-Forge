# Connected textures

In **Textures**, open **Connected textures** beside **Individual texture**.
The selected source texture shows its reviewed assemblies. **Browse all connected
images in this bank** exposes the bank catalog with live search. Select a search
result to change the preview. Mouse-wheel zoom uses nearest-neighbor scaling by
default. The **Trilinear filtering** checkbox smooths scaled previews;
**Rotate preview 180°** directly below it flips the preview without changing exports.
double-click a source part to open its original texture, including across banks.
**Export PNG + layout…** saves the original-resolution RGBA image and a version-2
JSON sidecar containing each part's bank, index, frame, dimensions and position.
Image selection, search, browse state, zoom and rotation stay active within the
current window. Viewer sessions are no longer saved or restored.

Only contiguous flat artwork is included. The earlier 554 automatically packed
actor/prop atlases have been removed: shared model membership does not prove one
image. Troff's body textures 0F78–0F8F do not form a flat portrait and are excluded.
A 3D preview is never substituted for the assembled image. Actual separately
stored Troff portrait artwork is eligible, just like other signs and portraits.

## Reviewed catalog, 2026-10-05

The runtime JSON contains **149 images**, ranging from two to sixteen parts,
including multirow grids. Examples include the 64×64 ship hatch, wooden and iron
doors, temple reliefs and inscriptions, Kong portraits, a Rareware pub sign,
DK logos, foliage backgrounds and decorative coin images. There are 124 layouts
using table 25 and 26 using table 7; one layout uses both. No table-14 layout has
yet been approved. These counts overlap for the cross-bank layout.

The hatch is **0610 left of 060F**, established by Map 43's shared UV boundary.
The Bananaport pad and five face layouts retain their explicit Randomizer
left/right mappings. Additional layouts were reviewed on contact sheets;
acceptance does not assert an official asset name or a unique complete original
sheet. Names describe the visible motif. Identical assembled pixels are merged,
and approved larger layouts suppress contained copies. True variants remain.

DK64 uses **source_v = render_v**: `mesh_decoder._uv` preserves the T direction,
`render_data` forwards it unchanged, and `texture_upload_rows` uploads top-first
rows without a flip. The horizontal hatch and multirow pub sign were checked
against those source rows. Artwork can therefore appear upside down in the raw
source orientation. No automatic rotation, mirroring, resampling, colour change,
alpha compositing or invented transition is applied. Pixels are copied row by row.

Before rendering or exporting, sources, dimensions, integer coordinates, bank
and frame are checked. Bounds, overlap and complete rectangular coverage are
mandatory; missing sources fail the whole image rather than filling placeholders.
Each piece refers to a fixed ROM entry. Animation frames are separate entries;
`frame: 0` means that entry itself, and nonzero frame requests are rejected rather
than silently ignored. Known sequence siblings cannot form one candidate image.

## Reproducible offline discovery

The tracked tool separates discovery from runtime. All source pixels and generated
contact sheets remain in ignored `local_output/connected/`; the repository contains
layout metadata only. Run from the repository with the local Python environment:

```powershell
.venv/Scripts/python.exe -m tools.discover_connected_textures local/roms/dk64_us.n64 --output local_output/connected --stage all
```

The stages `decode`, `geometry`, `search`, `sheets`, `review` and `publish` can also
run separately. `all` does not publish candidates. The decode stage writes
`pixels.npz`, source descriptors, ROM identity and explicit animation sequences.
Geometry scans shared triangle edges and opposite UV rectangle boundaries,
including curved boundaries flagged for separate panorama review. Geometry-stage
ROM identity must match the cache.

The bank-wide search groups equal-length edges, premultiplies RGB by alpha,
defers transparent/low-information edges, shortlists twelve neighbours using
squared pixel distance, then compares seam error against adjacent interior rows.
Reciprocal shortlist matches and UV offsets form larger groups. Overlapping groups
are refused; only fully covered rectangles become candidates. Different source
sizes and banks are supported, including partial UV seams where two smaller
rectangles meet one larger rectangle. Curved adjacency and common model usage alone
never approve a layout. The threshold of 16 is a DK64 search heuristic, not proof.

Candidate IDs hash source IDs and normalized positions. `reviews.json` stores
accepted/rejected/unresolved decisions and reasons, surviving reruns. Contact
sheets include IDs, sources, size and previews. `--stage sheets --pending` shows
unreviewed noncontained reciprocal matches first. Contained candidates are retained
for recovery when a larger group is rejected, but suppressed at publication when
the approved larger layout already contains them.

```powershell
.venv/Scripts/python.exe -m tools.discover_connected_textures local/roms/dk64_us.n64 --output local_output/connected --stage review --key CANDIDATE_ID --decision accepted --reason "Visually checked continuous image" --name "Visible motif"
.venv/Scripts/python.exe -m tools.discover_connected_textures local/roms/dk64_us.n64 --output local_output/connected --stage publish
```

Publication reads explicit accepted decisions, validates coverage, removes exact
pixel duplicates and attaches the decoded source descriptors. Review decisions
and pixels remain local; accepted layout evidence is embedded in the catalog.
Repeat the search against another ROM only after regenerating its decode cache.

## Coverage and limits

- Nonempty sources: table 25 **6,011**, table 7 **993**, table 14 **167**.
- Decoded images with known descriptors: **3,902 + 557 + 103 = 4,562**.
- **2,609** nonempty sources lack a usable image descriptor or are palettes;
  they are recorded, not guessed into pictures.
- **938** models loaded; **214** entries were empty/unsupported; no model-load
  exception was recorded.
- **14,405,416** directed bank-wide edge comparisons; **4,910** active candidates.
- **360** active candidates visually reviewed: 151 accepted, 209 rejected.
  Deduplication/containment plus the seven independently reviewed seeds yield
  the 149-image runtime catalog. Forty-six old decisions are retained for
  candidates retired by the animation-sequence filter.
- **4,550** candidates still await review and do not appear in the application.

This is a measured search result, not a claim that every DK64 split image is found.
Unknown decoding descriptors, further visual review and larger panorama recovery
remain open. Curved candidates are deliberately not automatically accepted.
Machine-readable counts are in [connected-texture-audit.json](connected-texture-audit.json).

## Validation

Twenty-two focused tests passed. Every runtime layout was checked pixel-for-pixel
against each independently decoded source part. Negative tests cover missing
sources, wrong dimensions, gaps, overlaps, bounds, invalid banks/frames, and
noninteger coordinates; a synthetic mixed-size, multirow, cross-bank image checks
row copying. Tests also exclude Troff model skin and known animation siblings.
Native Qt checks exercised source navigation across banks, live search, correct
preview selection, session restoration and PNG/layout export. Hatch and pub-sign previews were visually
inspected. Before publication on 2026-10-05, the complete application suite ran
200 tests: 198 passed and two optional checks were skipped (111.071 seconds).
The older 127-test reference result is separate and was not rerun. See
[status.md](status.md) for the validation record.
