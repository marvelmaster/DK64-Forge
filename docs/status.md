# Project status and open work

**Updated 2026-10-07.** The JFG feature-parity milestone is complete. The subsequent content/material expansion described below is implemented. Forge remains an experimental DK64 US revision-0 viewer/exporter; the four requested areas are substantially expanded, but are not fully runtime-faithful.

## Latest updates (2026-10-07)

Final pre-push validation: full UI import passed and all **22 focused tests**
(animation switching, ROM picker, level clicks and workspace regressions) passed
in **25.019 seconds**, with offscreen Qt. Local ROM inputs were available and no
checks in this run were skipped.

- Levels: placed-content checkbox precedes interpolation. Fungi Forest night and
  ROM-fog controls removed. Viewport picking respects culled back faces and source
  texture-alpha holes; click jitter no longer rotates the camera. Six focused
  checks passed, including clicking a placed object in the loaded Japes viewport
  and verifying selection/highlighting in its list.

- Animation switching reuses character geometry/materials, prepares uncached clips
  in background workers and cancels superseded sampling. Characters cache up to
  24 clips with a 64 MiB matrix-payload limit; actors cache up to 12 sampled clips.
  Measured DK clip preparation fell from 153–347 ms to 27–173 ms for five clips;
  see [animation-workspace.md](animation-workspace.md) for scope and measurements.

- Session menu and automatic viewer-state persistence removed. The native ROM
  picker instead remembers the last successfully loaded ROM directory, including
  command-line loads and application restarts. Personal clip libraries remain.

- Audio: 746 previously unlabelled IDs gained sourced community descriptions
  (671 distinct PCM samples); 920 of 1,126 playable IDs now have labels, while
  206 remain unnamed. Every accepted archive sample matches the ROM byte-for-byte.
  Ambiguous source names and unreliable wording are excluded. Source wording,
  identity fingerprints, exclusions and reproducible tooling are documented in
  [audio.md](audio.md).
- Sound playback now maps game ID `n` to bank index `n - 1`; ID 0 is a non-playable
  no-sound entry. The former offset could associate a name with the next sample.
- Texture and connected-image previews offer trilinear filtering followed by a
  180-degree rotation checkbox. Exports retain original pixels/orientation;
  these preview toggles apply within the current window. See [textures.md](textures.md) and
  [connected-textures.md](connected-textures.md).
- The audio module's import-order startup regression is fixed. Full UI import,
  41 focused catalog/audio/texture-assembly/workspace tests passed (30.510 seconds,
  offscreen Qt); catalog regeneration was byte-identical. Earlier full-suite
  validation records below retain their
  original dates and are not presented as a fresh full-suite run.

## Supported features

| Area | Available now |
|---|---|
| Models / Characters | Five playable Kongs; normal, weapon, instrument and low-poly variants; skinned playback and glTF export; DK bongos attached to the actor/scene origin |
| Animation names | Table-13 ownership plus direct source-call evidence; Tiny Pony Tail Twirl, Chunky Primate Punch, Lanky Orangstand/Baboon Balloon and Diddy Rocketbarrel routes identified |
| Character materials | ROM combiner mux, primitive/environment colours, one/two-cycle evaluation, shade-only materials, preview directional lighting, ROM RGBA16 mip levels and separate manual eye/colour selection, mouth opening and automatic Kong blinking |
| Other models | Static actor/prop viewing and GLB export; confirmed clips first, optional unassigned-clip filter, rigid-joint playback and animated GLB export; embedded prop matrix tracks and animated prop GLB export, billboard quads and prop texture playback/crossfades |
| Levels | Map geometry at the game's 1/3 map scale with texture-frame playback; setup props placed from ROM records; setup actors and character spawns drawn with the models of the game's definition tables (markers only where no model exists); click selection, double-click focus, named placement/category filters, current-pose selection export and curated enemy animation; procedural effect-7 scrolling surfaces |
| Textures | Banks 25, 7 and 14; usage-derived decoding (including 103 HUD images in bank 14), thumbnail grid, usage links, sequence playback, pixel preview, 149 reviewed flat images (multirow/cross-bank support), strict coverage validation and PNG/layout export |
| Animation workflow | Two synchronized clip previews; favorites and personal names/associations scoped to each model/variant and ROM; recovered ownership remains separate |
| ROM picker | Native file dialog opens in the last successfully loaded ROM folder across restarts; viewer-session save/restore removed |
| Tiny hair | Optional source-derived procedural diagnostic preview/export; disabled by default because world motion and collision-anchor inputs are approximated |
| Audio | 174 songs with ROM reverb settings, 1,126 playable SFX, 920 sourced sound labels, correct one-based sound IDs, WAV/MP3 export |

Normal-model counts from the current ROM descriptors:

| Kong | Browser clips | Statically owned | Derived labels | Owned without a semantic label |
|---|---:|---:|---:|---:|
| Donkey Kong | 183 | 94 | 76 | 18 |
| Diddy | 175 | 107 | 87 | 20 |
| Tiny | 165 | 96 | 69 | 27 |
| Chunky | 184 | 98 | 77 | 21 |
| Lanky | 171 | 99 | 73 | 26 |

Browser clips are structurally compatible; that alone does not establish ownership. Direct source calls supplement Table 13 because special moves can bypass its play-slot lookup. Labels are evidence-graded interpretations, not official names. Diddy's single-pose `0x0588` remains excluded from playback.

## Evidence for the requested changes

- Special moves were traced through source calls and inspected in rendered contact sheets: Tiny `0x01E5` Twirl, Chunky `0x02A7` Punch, Lanky `0x0160` Orangstand entry, and Diddy `0x0122` Rocketbarrel forward/steering pose. The complete historical label catalogue has not received an exhaustive visual review.
- Low-poly entries 2/4/7/10/14 retain the corresponding Kong skeleton mappings. DK's bongos use actor entry `0xA5`, attached at scale 1.25; their source-selected script-299 clips now play on the same preview clock and export as a separate skin. Script waits and speed changes are still diagnostic.
- ROM parsing found 222 prop and 74 map texture-animation descriptors. Animated frames use table 7 according to the loader; static geometry images use table 25. The previous size-based table fallback has been removed. Map segment bindings are resolved per chunk, including shared group 255, following `8062EDA8` / `80655DD0`.
- Japes has 428 setup props, 50 setup actors and 29 character spawners. Missing/unsupported prop geometry is represented by markers.
- Map chunk pieces (2026-10-03):
  - Every `G_ENDDL`-terminated piece of a chunk display list is drawn, and each piece gets its own segment-6 base.
  - Two addressing conventions exist: *relative* (354 chunks) and *absolute* (186 chunks). 360 shared sub-lists are drawn through their callers only.
  - This fixes the misjoined dark panels in Funky's store and other interiors, and draws about 50 % more map geometry that was previously missing.
  - The per-piece vertex rule is structural. The game's sub-record loader is not traced.
- Actor meshes (2026-10-03):
  - Setup actors resolve behaviour + 0x10 in `D_global_asm_8074E8B0`; character spawners index `D_global_asm_8075EB80` by enemy value. Both tables come from the ROM, and the names match their models. Across all maps 697 of 819 setup actors and 1,201 of 1,272 spawns have a model.
  - Every actor starts at 0.15 scale (`func_global_asm_806134B4`). Setup scale × 0.15 is from `code_8D3E0.c`; spawner byte × 5 / 255 × 0.15 was read from `func_global_asm_80726744`'s MIPS code.
  - Map geometry is drawn at `guScale(1/3)` (`func_global_asm_80650ECC`). With it, Japes spawns lie a median of < 40 units from map vertices instead of ~930.
- Tiny's idle/walk/run hair channels are constant in the clips. Ordinary motion is generated by the game's two eight-link procedural chains (`806F1A18` onward), not missing mesh weights. The diagnostic reproduces the pendulum/rest-angle/adjustment route with head-Y proxy inputs; an exact runtime comparison is outstanding.

## Performance (2026-10-03)

| What | Before | After |
|---|---:|---:|
| Japes frame time with placed content (1,289 draw batches) | 307 ms | 19 ms |
| Map 0x1A frame time (2,103 draw batches) | 279 ms | 29 ms |
| Decoding all 3,947 used textures | 3.5 s | 0.12 s (bit-identical output) |
| Loading Japes | 0.24 s | 0.11 s |
| Loading map 0x1A | 0.74 s | 0.32 s |

What made the difference:
- **Frame time:** the per-frame cost was PyOpenGL overhead, not the GPU.
  - `glGetUniformLocation` ran ~19 times per batch every frame; locations are now looked up once per program.
  - PyOpenGL ran a `glGetError` check after every call; this is now off in the render path. Shader compile and link status are still checked.
  - Material uniforms are only re-sent when the material changes.
  - Transparent batches are sorted by a cached centroid instead of averaging every vertex each frame.
- **Loading:**
  - Texture decoding and the dxt=0 de-interleave use NumPy instead of per-texel Python loops.
  - A texture's alpha class (opaque / mask / blend) is computed once per texture instead of per triangle.

Remaining Python cost is the per-batch draw loop (~0.02 ms per batch). Merging batches that share a material across placed instances would be the next step, if larger scenes need it.

Measure with `experiments/phase1_static_dk/tools/bench_viewport.py` (real OpenGL; Qt's offscreen platform has no GL context).

## Viewer expansion (2026-10-04)

Confirmed actor routes and level playback, independent eye/colour controls, current-view and selected-object export, pose-dependent preview lighting, effect 4, background loading, caches, frustum rejection, texture thumbnails/usage navigation and sequence playback are implemented. Enemy baked colours, independent placement loading and animation-switch playback are corrected; technical model/level controls are hidden. Evidence and practical limits: [viewer-expansion.md](viewer-expansion.md).

## Level exploration and animation controls (2026-10-04)

Level selection/focus and placement filters, five further confirmed enemy routes, named/owned clip ordering, optional interpolation checkboxes, animated prop export and source-derived HUD decoding are implemented. Instructions, ROM evidence and limits: [level-exploration.md](level-exploration.md).

## Remaining work

| Area | Still open |
|---|---|
| Animation names | Further generic Actor names/ownership (501 known clip names/contexts across the current catalog, with 105 fully sampled model/clip pairs); exhaustive visual review of existing labels; further special moves and effects (including Mini Monkey/Monkeyport, Hunky Chunky/Gorilla Gone). A gameplay state does not necessarily have a unique stored clip. |
| N64 rendering | Game-supplied RSP light state; complete TMEM layouts, fixed-point combiner rounding, coverage/dither/blender, per-pass fog state and exact texture-LOD behavior. Preview lighting is a fixed key light. glTF retains RDP state in metadata but displays an approximation. |
| Dynamic textures | Gameplay facial adjustments, Diddy Rocketbarrel-specific texture handling, other procedural surface effects and exact RDP crossfade behavior. Single-player Kong blinking, prop crossfades and effect-7 scrolling are available. |
| Models | Prop track triggering, runtime speed/direction/transition state; ownership of further generic actor clips and game timing; exact bongo script timing; Toy Monster component assembly and Zipper clip ownership. Prop matrix playback and source-selected bongo clips are implemented. |
| Levels | General spawn conditions and actor scripts; procedural effect IDs 2/3; camera-dependent portal visibility and dynamic surface transforms. Billboard quads, effect-7 surfaces, source prop rotation order and placed texture playback are implemented. The Fungi night and ROM-fog UI options have been removed. |
| Tiny hair | Actual world speed/heading and collision-anchor movement, exact game float behavior and runtime comparison. The optional preview is diagnostic and must not be treated as bit-exact restoration. |
| Texture metadata | Usage-derived HUD formats/palettes, unused texture formats and remaining undecodable usages. Table 14 has 103 automatic HUD decodes; 64 entries still require usable draw-use evidence. |
| Animation runtime | Ordinary AAS adjustments beyond the Tiny diagnostic; transition/blend route; exact game interpolation (optional desktop smoothing is available); per-clip timing/scalars, endpoints and loop semantics. Origin-centered exports omit actor/world placement. |
| Focused runtime checks | Tiny's 22 stored-UV hilite triangles; runtime-verified reference clips for the four other Kongs; gameplay meaning of prefix translation. |
| Audio | Per-call SFX pitch/volume; sustain/mid-note pitch bends; controller 65; console rounding. Four of 180 ADPCM loop states remain unresolved, affecting three SFX samples. |

Only the captured interior interval of DK Entry 4 establishes 30 adjusted units/s. Other playback uses this rate diagnostically. Browser wrapping and glTF interpolation do not prove game looping or interpolation.

## Validation

Current application/research checks and visual validation are recorded alongside the implementation. ROM-derived tests and capture artifacts remain local under existing ignore rules. The reference research suite has **127 passing tests**; the application suite has **200 tests of current coverage**, including the native Windows OpenGL checks, for **327 total coverage**. The pre-push review additionally fixed texture border clamping, preservation of ROM mip levels, centroid-cache identity reuse and playback timers continuing after a window closes. Special-move/Tiny-hair contact sheets and native OpenGL previews of the models, level-content and texture panels were inspected. Native captures of billboard quads, animated prop parts and the scrolling map surface were also inspected. No emulator was running for a new game-to-viewer comparison.

Tab details: [models.md](models.md), [textures.md](textures.md), [audio.md](audio.md).

The subsequent runtime-preview implementation and its evidence are detailed in [runtime-preview.md](runtime-preview.md).

A custom jaw-opening preview is available for all five Kongs; zero retains the ROM expression. See [runtime-preview.md](runtime-preview.md).

## Level playback performance (2026-10-04)

Level playback retains map and placed-object geometry, transforms, vertex buffers and GPU texture handles. It updates changed texture images and procedural surface UVs. The newer actor path additionally copies precomputed pose/normal cycles into existing buffers. Repeated textures are shared; compatible opaque/masked placement batches are combined while blended batches and billboard pivots stay separate.

The first optimization reached approximately 22 FPS in a local native Windows Japes benchmark with all 507 placements enabled, versus the 2 FPS reported in the user capture. Before the change, CPU map decoding took 109–118 ms and placement rebuilding 116–118 ms per warmed tick, before GPU uploads. After the change, the isolated native update median was 6.8 ms, with paint/event processing around 12.6 ms. Placed content uses 269 shared textures and 677 batches instead of 1,058 textures and 1,233 batches; GPU texture handles survive playback. These figures describe this machine and viewport, not a guaranteed rate for every map.

Regression checks compare cached frames and scrolling UVs against full decoding and ensure that playback does not decode map geometry or rebuild placements. All 21 maps with texture-animation descriptors accept the cached binding plan; the main seven adventure-map overlays were also exercised. No UI framework migration was needed for this fix.

The follow-up removes the 33 ms render scheduling limit: a precise 16 ms timer requests frames, while elapsed time advances the animation clock at 30 ticks/s and catches up after delayed callbacks. OpenGL state, individual shader uniforms and texture bindings are sent only when they change within a mesh pass. The five-second native benchmark now measures **63 rendered FPS, 6.5–6.7 ms paint time**, with 149 animation ticks. This was reproduced in both 1180×760 and 1902×974 logical-pixel windows; the larger run used the application's OpenGL 3.3 setup and zoomed in twice. A Japes framebuffer comparison against the previous draw implementation was byte-identical. Actual display rate still depends on hardware and presentation settings.

The level exploration update adds picking/focus/name filters, optional interpolation, five additional enemy movement routes, embedded prop animation GLB export and 44 HUD texture decodes. See [level-exploration.md](level-exploration.md).

## Animation workflow and sessions (historical checkpoint, 2026-10-04)

At this checkpoint, side-by-side clip comparison, model-scoped favorites/personal labels and ROM-bound viewer sessions were implemented. Viewer sessions were subsequently removed on 2026-10-07; comparison and personal libraries remain. K. Lumsy and Beanstalk animate/export with their irregular skeletons; the Zipper supports 45 compatible clips without claiming ownership. Toy Monster displays and exports its animated skeleton; its component mesh assembly remains open. HUD coverage is now 103/167, with two source-defined overlay sequences. See [animation-workspace.md](animation-workspace.md) for controls, validation and remaining limits.

## Windows access-violation report (2026-10-04)

A screenshot showed a native Python/Qt memory-read error. The user clarified that they had not observed a Forge crash; the message may have originated from an agent test process. No responsible process or reproducible trigger was identified. A subsequent process inspection found no running Python process associated with this repository. The completed test logs recorded success. This remains an unattributed report, not a confirmed or fixed application defect; no crash-related code change was made. If it recurs, record the action, affected window and process before closing the error, and run with Python faulthandler enabled.

## Connected textures (2026-10-05)

The artificial model atlases have been removed. A separate cached search examined
4,562 decoded sources in banks 25/7/14, 938 models and 14,405,416 directed edge
comparisons. The runtime catalog now contains 149 visually reviewed contiguous
flat images with exact source-row copying, multirow layouts and per-part bank
metadata. Missing sources, dimensions, bounds, frames, overlaps and gaps are
validated; known animation siblings are excluded. Twenty-two focused tests passed,
including pixel checks for every catalog image and native search/navigation/export.
4,550 active candidates remain unreviewed and are excluded from the UI. Unknown
texture descriptors and further panorama review remain open. See
[connected-textures.md](connected-textures.md) and its machine-readable audit.

Validation refreshed before publication on 2026-10-05: the complete application
suite ran **200 tests**, with **198 passing and two optional skips** (111.071 seconds).
The 22 focused connected-texture checks also passed, including every catalog
image's source-pixel comparison, mixed-size discovery, stable candidate IDs,
native navigation/export and session restoration. The previously recorded
reference suite has 127 tests and was not rerun for this texture-only change.
Current coverage is 200 application checks plus 127 reference checks; the count
of 327 describes coverage, not one combined fresh test run.
