# Viewer expansion — 2026-10-04

## Animation ownership and level motion

Character dropdowns put source/Table-13-owned clips first and distinguish **Confirmed** ownership from **Compatible only**. A compatible channel count alone never establishes ownership. Semantic names and question marks retain their existing evidence grades; confirmation of ownership does not confirm every move name.

Other models prefer confirmed candidates before compatible candidates. The curated non-Kong registry resolves Table-13 scripts through the current ROM, accepting direct clip-setting opcodes:

| Table-5 model | Script | Evidence in CC0 dk64_decomp |
|---|---|---|
| 0x19 Blue Beaver | 0x1F8 | 806AD54C / code_B1F60.c; movement configuration via 8072B79C |
| 0x1B Zinger | 0x250 | 806B486C/806B48B8 -> 806B42A8 / code_B7490.c |
| 0x20 Green Klaptrap | 0x232 | 806B75F4 -> 806B6DB0 / code_BB300.c |
| 0x23 Skeleton Klaptrap | 0x237 | explicit spawn/play call in 806B6C88 / code_BB300.c |

Model identity follows the actor dispatch table at 8074C0A0 and the existing ROM model-definition tables. The registry deliberately does not generalize to similarly shaped variants.

Levels play these curated clips at the recorded placements. Short pose/normal cycles are prepared during loading; each runtime tick uses vectorized copies into retained GPU buffers. Japes has 18 supported animated instances (Beavers and Zingers) among 507 placements. Unsupported actors remain at rest. Scripts, navigation, collision and spawn conditions are not simulated; 30-Hz timing and interval wrapping remain preview policies.

## Eyes, colour and export

Characters have separate **Eyes open / half closed / closed** and **Clothing / colour frame** controls. Automatic blinking overrides only the eye selector; colour variants remain independent. The mouth slider still adds the existing custom jaw offset.

**File -> Export -> Current view / pose** exports GLB or PNG. GLB freezes displayed geometry, the current pose including the mouth offset, textures, generated UVs, attachment pose and camera-facing billboard orientation. It includes a glTF camera. PNG captures the actual viewport, including its shader appearance. The Models/Levels export button uses the same snapshot path. Standard animation exports retain their authored clips.

In Levels, Ctrl/Shift-select object rows and choose **Export selected objects** to export just those objects in world coordinates at the current animation/texture tick. Map-chunk selection is respected by current-view export. GLB materials remain approximations with RDP metadata; the exact combiner, fog and coverage are not portable standard glTF materials. Use PNG for exact rendered appearance.

## Rendering

- Actor preview normals now follow the sampled pose, including placement rotation for level actors. Unlit/baked vertex colours are preserved. The key light remains a preview light, not reconstructed game RSP light state.
- Map fog reads header byte 8 bit 0 (8062F050). 80659110 supplies black or Aztec RGB 138/82/22, and projected-depth limits 990/999. Fog is opt-in: the distant overview camera makes the original depth range very dense. Free-camera projection and broad scene application differ from the game's per-pass fog/render-mode state.
- Procedural effect 4 adds the IA8 two-tile surface from Table 7 image 0x3E0 with independent scroll rates -5/-2, following 8063CF3C/8063D1D8. It is present in maps 34 and 176. Its combiner preserves translucent alpha. Effect 7 remains supported. Effects 2/3 and exact blender/coverage are still unresolved.
- Conservative sphere/frustum rejection skips offscreen static batches, including rotation-safe billboard bounds. Moving actor overlays are retained conservatively rather than culled with rest-pose bounds. This is not portal or occlusion visibility.

## Loading and textures

Model selection, placement preparation and texture-bank scanning run in serialized background jobs with an activity bar and stage messages. A newer selection invalidates older results, and closing the window cancels delivery. GL uploads and Qt widgets remain on the GUI thread. The initial lightweight model catalog and explicit manual clip sampling still run synchronously.

The model browser keeps a 12-entry decoded asset cache; placement assets, decoded textures, scanned texture banks and thumbnails are reused. Short actor cycles are cached for the loaded level. These are in-memory caches scoped to the ROM/window.

Textures offer a thumbnail grid/list switch, incrementally decoded icons, double-clickable model/map references, and a sequence strip with frame selection and playback. Sequences come from actual ROM descriptors, never consecutive texture IDs. Actor-slot playback is a manual preview; map/prop sequences use descriptor ticks at 30 Hz. The strip shows discrete frames; prop interpolation still belongs to the model/level renderer. Unknown HUD/unreferenced formats remain undecoded.

## Validation and limits

Local tests cover all four curated models, independent eye/colour changes on all five Kongs, unchanged static vertices during level animation, snapshot/selected-object GLB contents, stale-job rejection, frustum rejection and effect-4 cached/full-decode equivalence. Native Windows previews and exports were inspected. All 156 application tests and 127 reference/research tests pass (283 total).

A five-second native Japes run at 1420x900 logical pixels, zoomed to 45% of its initial camera distance, measured about 63 FPS and 149 animation ticks with all placements enabled, including 18 animated actors. Rates vary with hardware and concurrent load; a separate run measured 54 FPS. The first unoptimized actor implementation repeatedly skinned instances in Python and was replaced before completion. No UI framework migration was needed.

This improves each requested area without claiming an exact game renderer or gameplay simulation. Further clip ownership, script-specific animation timing, game lights, per-pass fog/blending, remaining procedural effects and portal visibility are tracked in [status.md](status.md).
