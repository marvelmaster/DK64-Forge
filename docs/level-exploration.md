# Level exploration and animation — 2026-10-04

## Selection and filters

Enable **Show placed props and actor spawns** (now directly above **Interpolate animation**), then click a placed object in the level to select it. Ctrl/Shift-click toggles additional selections. Dragging still orbits the camera. A double-click on the level object or its list row focuses the camera; Reset view restores the whole level. The list and selection label show the model/prop name and placement ID. A cyan rectangle marks the selected geometry.

The placement search filters names and IDs. All objects, Actors / enemies, Props and Technical markers filter both the list and viewport. Actors / enemies includes named NPCs and other setup actors, not just enemies. Markers represent entries without decodable models. Selection export uses the current visible geometry and pose, including interpolation, instead of rebuilding rest poses. Current-view export respects the filter.

Picking uses current animated triangles and camera-facing billboard geometry. Opaque level triangles block objects behind walls. Texture-alpha holes are not sampled during picking; masked surfaces use their triangle outlines. These are viewer interactions, not game collision.

## Clips and interpolation

Confirmed clips sort before unassigned candidates; known labels are sorted for browsing. Other models has **Show other / unassigned actor clips**, which can hide unknown ownership. Clip search respects this filter and updates a visible result list as you type. Choosing a result changes the animation; typing alone preserves playback and selection. Static pose remains available. Autoplay skips hidden clips; changing a clip preserves Play/Pause.

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

The initial level-exploration update decoded 44 table-14 entries: 43 font files plus overlay 0x5F. Current coverage is 103/167; see [animation-workspace.md](animation-workspace.md). The font file ranges come from 80754A34 in the ROM, and layouts from 806FBEF0's tile/load commands (code_100180.c). Overlay 0x5F is I4 64x64, loaded by 80703CF8. Font formats include IA4, IA8, I4 and RGBA16; layouts are not guessed from file lengths. Glyph height is distinct from atlas height. The remaining 64 entries need usable draw-layout evidence. Known HUD images support thumbnails, zoom and PNG export; their usage rows describe HUD draw routines instead of linking to a model.

## Validation

Local tests cover five additional actor routes, exact sample endpoints, rotation-length preservation, merged-object subsets, ray hits, 44 HUD decodes, and sparse prop-export reconstruction against the preview (button/platform examples). Native Windows checks exercise object picking/focus/filtering, clip-filter search, interpolation toggling and prop export. Selection and HUD previews were inspected. ROM-derived tests and captures remain local under existing ignore rules.

The completed Windows application suite has 167 passing tests; the reference research suite has 127, for 294 total.

## Actor-name audit and eye/search fixes (2026-10-04)

Searchable animation selectors now display live result lists, matching asset-list search behavior. Small selectors remain dropdowns. Filtering never rewrites clip IDs and respects the ownership filter. Empty results leave the currently playing clip unchanged.

Eye updates upload textures only, retaining current positions, UVs and vertex shade. A paused native Windows framebuffer comparison for all five Kongs changed only the eye region. The reported whole-body lighting change was not reproducible in that controlled check; animated poses continue to change preview lighting normally.

The expanded catalog records 91 distinct constant animation calls for 42 exact models. Actor/model links come from ROM setup/enemy definitions and constant spawnActor calls. Dispatch handlers are read from 8074C0A0. Direct C calls on gCurrentActorPointer are checked against their ROM MIPS call targets/constants; opaque handlers are accepted only when the literal clip/script argument and the current-actor pointer are tracked. Movement initializer triplets use 8072B79C. Call-site evidence is retained in actor_route_catalog.py and animation tooltips. The catalog resolves 501 known clip names/contexts together with existing Kong labels. Labels from another actor do not establish ownership for the current model.

Before the irregular-skeleton update, a complete sample audit validated 98 model/clip pairs, filtered 18 incompatible routes, and found four unsupported skeleton/mesh structures (Toy Monster 0x27, K. Lumsy 0x44, Beanstalk 0x47 and Bananaporter Zipper 0x97). Source names may describe an action/script context rather than a precise movement; the ROM does not provide official clip names. Unknown names and additional model ownership still require source tracing or visual review. They remain explicitly unassigned. Level autoplay continues using the curated movement subset rather than arbitrary attack/defeat scripts.

After the eye/search/catalog changes: 170 application tests and 127 reference tests pass (297 total). Native live-search and Kosha clip-list captures were inspected.

## Follow-up: comparisons, irregular skeletons and sessions

The unsupported-structure audit above describes the earlier checkpoint. The four models now have skeleton support, including animated skeleton-only Toy Monster; K. Lumsy and Beanstalk animate/export, and the Zipper offers 45 compatible clips with ownership still unresolved. HUD coverage has increased to 103 entries, leaving 64 unknown. Clip comparison, personal associations/favorites are covered in [animation-workspace.md](animation-workspace.md). Viewer-session controls have since been removed; the ROM picker remembers the last loaded folder.

## Level controls and click selection (2026-10-07)

The Fungi Forest night and ROM-fog checkboxes have been removed. The level preview
uses the standard spawn set without the optional fog overlay. Hidden session
utilities no longer reference either removed control.

Picking now respects single-sided face culling, non-depth-writing map surfaces
and transparent source texture areas (nearest source-alpha lookup at the ray UV).
Opaque walls still block selection; filtered-out placements cannot be picked.
Small mouse jitter stays a click without changing the camera; dragging orbits as
before. Ctrl/Shift-click still toggles multi-selection, and double-click focuses.

All six checks in `tools.test_level_clicks` passed, including real Japes content
loading, viewport mouse-click delivery, object-list selection and highlight data.

Final pre-push run: all 22 focused animation-switch, ROM-dialog, level-click and
workspace checks passed in 25.019 seconds; full UI import also passed.
