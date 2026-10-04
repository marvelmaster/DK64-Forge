# Level exploration and animation — 2026-10-04

## Selection and filters

Click a placed object in the level to select it. Ctrl/Shift-click toggles additional selections. Dragging still orbits the camera. A double-click on the level object or its list row focuses the camera; Reset view restores the whole level. The list and selection label show the model/prop name and placement ID. A cyan rectangle marks the selected geometry.

The placement search filters names and IDs. All objects, Actors / enemies, Props and Technical markers filter both the list and viewport. Actors / enemies includes named NPCs and other setup actors, not just enemies. Markers represent entries without decodable models. Selection export uses the current visible geometry and pose, including interpolation, instead of rebuilding rest poses. Current-view export respects the filter.

Picking uses current animated triangles and camera-facing billboard geometry. Opaque level triangles block objects behind walls. Texture-alpha holes are not sampled during picking; masked surfaces use their triangle outlines. These are viewer interactions, not game collision.

## Clips and interpolation

Confirmed clips sort before unassigned candidates; known labels are sorted for browsing. Other models has **Show unassigned animations**, which can hide unknown ownership. Clip search respects this filter. Static pose remains available. Autoplay skips hidden clips; changing a clip preserves Play/Pause.

**Interpolate animation** is available in Characters, Other models and Levels, and is off by default. Off displays the stored samples. On smooths between adjacent poses using the existing elapsed-time clock. Character local transforms and generic actor composed transforms use quaternion rotation interpolation and blended translation/stretch. Level actors blend cached world-space vertex/colour samples to retain performance. This level smoothing can slightly shorten rapidly rotating limbs; it is a visual preview, not recovered game interpolation. The safe-interval wrap is also a preview policy. Scrubbing at an integer sample remains exact. Embedded prop tracks retain their existing source-derived key interpolation.

Five additional exact Table-5 models have curated routes:

| Model | Movement script | ROM initializer evidence |
|---|---|---|
| Kremling (0x30) | 0x1FB | 806AE588; call 806AE5BC -> 8072B79C |
| Klump (0x39) | 0x2AB | 806AEE84; call 806AEEF4 -> 8072B79C |
| Krossbones (0x41) | 0x2D7 | 806AFB58; call 806AFB90 -> 8072B79C |
| Spider (0x45) | 0x2F4 | 806AD9F4; call 806ADA38 -> 8072B79C |
| Kosha (0x60) | 0x35F | 806B0848; call 806B0B14 -> 8072B79C |

Enemy model and dispatch tables establish the actor/model relationship. Initializer constants were checked in the normalized ROM's MIPS overlay; direct clip opcodes are resolved through Table 13. Other variants are not promoted on skeleton compatibility alone. These movement labels describe a confirmed route; they do not assign separate semantic names to every internal clip. Japes now has 20 supported animated placements. Actors remain at recorded positions; AI and script/spawn conditions are not executed.

## Animated prop export

Select an embedded prop animation in Other models and use **Export model + animation GLB**. Export includes a forward cycle at preview speed 1 and 30 ticks/s. Sparse affine morph targets reproduce the part matrices, including pre/post transforms and shear. Positions at stored ticks match the preview. The final key returns to the initial pose for the preview loop. Consumers interpolate sampled weights, which approximates the trajectory between samples. Textures and vertex lighting are frozen at the initial state. Object-script waits, transitions and triggering are not simulated. Tracks without a forward cycle within 6000 ticks report a clear error instead of an unbounded export.

## HUD texture decoding

Table 14 now decodes 44 entries: 43 font files plus overlay 0x5F. The font file ranges come from 80754A34 in the ROM, and layouts from 806FBEF0's tile/load commands (code_100180.c). Overlay 0x5F is I4 64x64, loaded by 80703CF8. Font formats include IA4, IA8, I4 and RGBA16; layouts are not guessed from file lengths. Glyph height is distinct from atlas height. The remaining 123 entries need further draw-use evidence. Known HUD images support thumbnails, zoom and PNG export; their usage rows describe HUD draw routines instead of linking to a model.

## Validation

Local tests cover five additional actor routes, exact sample endpoints, rotation-length preservation, merged-object subsets, ray hits, 44 HUD decodes, and sparse prop-export reconstruction against the preview (button/platform examples). Native Windows checks exercise object picking/focus/filtering, clip-filter search, interpolation toggling and prop export. Selection and HUD previews were inspected. ROM-derived tests and captures remain local under existing ignore rules.

The completed Windows application suite has 167 passing tests; the reference research suite has 127, for 294 total.
