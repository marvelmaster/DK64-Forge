# Project status and open work

**Updated 2026-10-04.** The JFG feature-parity milestone is complete. The subsequent content/material expansion described below is implemented. Forge remains an experimental DK64 US revision-0 viewer/exporter; the four requested areas are substantially expanded, but are not fully runtime-faithful.

## Supported features

| Area | Available now |
|---|---|
| Models / Characters | Five playable Kongs; normal, weapon, instrument and low-poly variants; skinned playback and glTF export; DK bongos attached to the animated root |
| Animation names | Table-13 ownership plus direct source-call evidence; Tiny Pony Tail Twirl, Chunky Primate Punch, Lanky Orangstand/Baboon Balloon and Diddy Rocketbarrel routes identified |
| Character materials | ROM combiner mux, primitive/environment colours, one/two-cycle evaluation, shade-only materials, preview directional lighting, ROM RGBA16 mip levels and separate manual eye/colour selection, mouth opening and automatic Kong blinking |
| Other models | Static actor/prop viewing and GLB export; confirmed clips first, optional unassigned-clip filter, rigid-joint playback and animated GLB export; embedded prop matrix tracks and animated prop GLB export, billboard quads and prop texture playback/crossfades |
| Levels | Map geometry at the game's 1/3 map scale with texture-frame playback; setup props placed from ROM records; setup actors and character spawns drawn with the models of the game's definition tables (markers only where no model exists); click selection, double-click focus, named placement/category filters, current-pose selection export and curated enemy animation; procedural effect-7 scrolling surfaces and Fungi night spawns |
| Textures | Banks 25, 7 and 14; usage-derived decoding (including 44 HUD images in bank 14), thumbnail grid, usage links, sequence playback, pixel preview and bank-qualified PNG export |
| Tiny hair | Optional source-derived procedural diagnostic preview/export; disabled by default because world motion and collision-anchor inputs are approximated |
| Audio | 174 songs with ROM reverb settings, 1,126 SFX, WAV/MP3 export |

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

Confirmed actor routes and level playback, independent eye/colour controls, current-view and selected-object export, pose-dependent preview lighting, map fog, effect 4, background loading, caches, frustum rejection, texture thumbnails/usage navigation and sequence playback are implemented. Enemy baked colours, independent placement loading and animation-switch playback are corrected; technical model/level controls are hidden. Evidence and practical limits: [viewer-expansion.md](viewer-expansion.md).

## Level exploration and animation controls (2026-10-04)

Level selection/focus and placement filters, five further confirmed enemy routes, named/owned clip ordering, optional interpolation checkboxes, animated prop export and source-derived HUD decoding are implemented. Instructions, ROM evidence and limits: [level-exploration.md](level-exploration.md).

## Remaining work

| Area | Still open |
|---|---|
| Animation names | Further generic Actor names/ownership (501 known clip names/contexts across the current catalog, with 98 fully sampled model/clip pairs); exhaustive visual review of existing labels; further special moves and effects (including Mini Monkey/Monkeyport, Hunky Chunky/Gorilla Gone). A gameplay state does not necessarily have a unique stored clip. |
| N64 rendering | Game-supplied RSP light state; complete TMEM layouts, fixed-point combiner rounding, coverage/dither/blender, per-pass fog state and exact texture-LOD behavior. Preview lighting is a fixed key light. glTF retains RDP state in metadata but displays an approximation. |
| Dynamic textures | Gameplay facial adjustments, Diddy Rocketbarrel-specific texture handling, other procedural surface effects and exact RDP crossfade behavior. Single-player Kong blinking, prop crossfades and effect-7 scrolling are available. |
| Models | Prop track triggering, runtime speed/direction/transition state; ownership of further generic actor clips and game timing; exact bongo script timing. Prop matrix playback and source-selected bongo clips are implemented. |
| Levels | General spawn conditions and actor scripts; procedural effect IDs 2/3; camera-dependent portal visibility and dynamic surface transforms. Billboard quads, effect-7 surfaces, Fungi night substitutions, source prop rotation order and placed texture playback are implemented. |
| Tiny hair | Actual world speed/heading and collision-anchor movement, exact game float behavior and runtime comparison. The optional preview is diagnostic and must not be treated as bit-exact restoration. |
| Texture metadata | Usage-derived HUD formats/palettes, unused texture formats and remaining undecodable usages. Table 14 has 44 automatic font/overlay decodes; 123 entries still require draw-use evidence. |
| Animation runtime | Ordinary AAS adjustments beyond the Tiny diagnostic; transition/blend route; exact game interpolation (optional desktop smoothing is available); per-clip timing/scalars, endpoints and loop semantics. Origin-centered exports omit actor/world placement. |
| Focused runtime checks | Tiny's 22 stored-UV hilite triangles; runtime-verified reference clips for the four other Kongs; gameplay meaning of prefix translation. |
| Audio | Per-call SFX pitch/volume; sustain/mid-note pitch bends; controller 65; console rounding. Four of 180 ADPCM loop states remain unresolved, affecting three SFX samples. |

Only the captured interior interval of DK Entry 4 establishes 30 adjusted units/s. Other playback uses this rate diagnostically. Browser wrapping and glTF interpolation do not prove game looping or interpolation.

## Validation

Current application/research checks and visual validation are recorded alongside the implementation. ROM-derived tests and capture artifacts remain local under existing ignore rules. The reference research suite has **127 passing tests**; the application suite has **170 passing tests**, including the native Windows OpenGL checks, for **297 total**. The pre-push review additionally fixed texture border clamping, preservation of ROM mip levels, centroid-cache identity reuse and playback timers continuing after a window closes. Special-move/Tiny-hair contact sheets and native OpenGL previews of the models, level-content and texture panels were inspected. Native captures of billboard quads, animated prop parts and the scrolling map surface were also inspected. No emulator was running for a new game-to-viewer comparison.

Tab details: [models.md](models.md), [textures.md](textures.md), [audio.md](audio.md).

The subsequent runtime-preview implementation and its evidence are detailed in [runtime-preview.md](runtime-preview.md).

A custom jaw-opening preview is available for all five Kongs; zero retains the ROM expression. See [runtime-preview.md](runtime-preview.md).

## Level playback performance (2026-10-04)

Level playback retains map and placed-object geometry, transforms, vertex buffers and GPU texture handles. It updates changed texture images and procedural surface UVs. The newer actor path additionally copies precomputed pose/normal cycles into existing buffers. Repeated textures are shared; compatible opaque/masked placement batches are combined while blended batches and billboard pivots stay separate.

The first optimization reached approximately 22 FPS in a local native Windows Japes benchmark with all 507 placements enabled, versus the 2 FPS reported in the user capture. Before the change, CPU map decoding took 109–118 ms and placement rebuilding 116–118 ms per warmed tick, before GPU uploads. After the change, the isolated native update median was 6.8 ms, with paint/event processing around 12.6 ms. Placed content uses 269 shared textures and 677 batches instead of 1,058 textures and 1,233 batches; GPU texture handles survive playback. These figures describe this machine and viewport, not a guaranteed rate for every map.

Regression checks compare cached frames and scrolling UVs against full decoding and ensure that playback does not decode map geometry or rebuild placements. All 21 maps with texture-animation descriptors accept the cached binding plan; the main seven adventure-map overlays were also exercised. No UI framework migration was needed for this fix.

The follow-up removes the 33 ms render scheduling limit: a precise 16 ms timer requests frames, while elapsed time advances the animation clock at 30 ticks/s and catches up after delayed callbacks. OpenGL state, individual shader uniforms and texture bindings are sent only when they change within a mesh pass. The five-second native benchmark now measures **63 rendered FPS, 6.5–6.7 ms paint time**, with 149 animation ticks. This was reproduced in both 1180×760 and 1902×974 logical-pixel windows; the larger run used the application's OpenGL 3.3 setup and zoomed in twice. A Japes framebuffer comparison against the previous draw implementation was byte-identical. Actual display rate still depends on hardware and presentation settings.

The level exploration update adds picking/focus/name filters, optional interpolation, five additional enemy movement routes, embedded prop animation GLB export and 44 HUD texture decodes. See [level-exploration.md](level-exploration.md).
