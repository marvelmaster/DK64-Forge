# Models and Levels

**Models** contains **Characters** and **Other models**. Characters provides the five playable Kongs, animation ownership/labels, timing, joint inspection and glTF export. Other models lists table-5 actors and table-4 props. **Levels** lists table-1 map geometry.

## Characters

Choose Normal, Weapon, Instrument or Low poly. All five low-poly meshes use the corresponding regular skeleton. DK's instrument variant attaches the separate bongos actor at scale 1.25 and shares his actor origin (the scene root), independently of his animated pelvis. The bongos also play their own source-selected script-299 clips on the same preview clock, with diagnostic timing. Animated exports include their separate skin and channels. Other instrument variants use their dedicated models.

The ownership filter includes Table-13 routes and direct source calls. Special moves now include Tiny Pony Tail Twirl (`0x01E5`), Chunky Primate Punch (`0x02A7`), Lanky Orangstand (`0x0160` entry, `0x0161` idle, `0x0162` run, `0x0163` walk), and Diddy Rocketbarrel (`0x0121` reused gun/steering pose, `0x0122` forward, `0x0123` neutral, `0x0124` opposite, `0x0125` transition). Names remain interpretations of source and visual evidence.

**Clothing / colour frame** controls colour variants independently of **Eyes open / half closed / closed**. **Automatic eye blinking** follows the single-player Kong routine with repeatable randomness and overrides only the eye selector. The **Mouth opening** slider adds 0–35° jaw rotation; zero preserves the authored expression. Current-view export includes this adjustment; ordinary animation export retains the original clip. The optional Tiny hair checkbox enables a source-derived procedural diagnostic and applies to model-plus-animation and animation-only exports. Idle/walk/run clips contain constant hair channels; the game supplies the additional motion. The diagnostic substitutes head movement for collision anchors and assumes zero world heading/speed, so it is disabled by default.

## Other models

Search names or decimal/hex IDs; filter actors or props. The panel shows names/evidence, triangles/batches, decoded images and skipped display-list commands. Magenta means unresolved texture data.

Selecting an actor automatically scans Table 11 against its skeleton and prefers a source-confirmed candidate, then starts the first compatible candidate that passes complete interior sampling. The Animation dropdown loads automatically and supports animated GLB export. Kong models reuse the Character browser names; confirmed actor routes have source-based labels. Unknown ownership is explicitly marked **Unassigned animation**, with compatibility evidence in the tooltip. Switching clips preserves Play/Pause. Selecting a prop automatically plays its first embedded matrix track, or its texture animation when no track exists. Models without available motion stay static. Pause remains available; choosing Static rest pose or changing tabs stops playback. Complete interior sampling validates a selected clip. Compatibility alone does not establish ownership: clips, timing, runtime adjustments and endpoints remain diagnostic. **Static rest pose** restores the original geometry; **Export current view / pose** freezes the displayed pose, textures, attachments and camera as GLB, or captures PNG.

For props, **Play** advances ROM texture animations and embedded matrix tracks. Select an animation and scrub or play it. The technical texture-frame and script-speed controls are hidden; the preview uses speed 1. Part matrices include the stored pre/post transforms and interpolated TRS keys. Track triggering, forward looping and speed are preview choices rather than execution of object scripts. Static GLB exports capture the currently displayed prop pose. Billboard props decode their stored quad/UV/texture records and face the preview camera; GLB stores fixed quad geometry with billboard metadata.

## Levels

Map playback selects texture frames using each descriptor's ticks-per-frame, with a diagnostic 30-tick/s preview. Dynamic segments are bound per chunk, as in the game's loader. All chunks are shown; the technical chunk selector and its search are hidden. Game portal visibility is not simulated. Procedural effect 7 surfaces include their source-selected texture, transparency and scrolling tile origin. Other procedural effect IDs are reported in Notes.

**Show placed props and actor spawns** reads setup table 9 and character-spawner table 16. Supported model-two meshes are placed with ROM position, scale and rotation records.

**Actors** use the game's own definition tables, read from the ROM:

| Source | Lookup | Scale |
|---|---|---|
| Setup actors | behaviour → `D_global_asm_8074E8B0` | row scale × 0.15 |
| Character spawners | enemy value → `D_global_asm_8075EB80` | byte × 5 / 255 × 0.15 (`func_global_asm_80726744`) |

Both are drawn at their 12-bit y rotation, in rest pose with their first texture frames. Entries whose definition has no model (controllers, effect spawners) and props without geometry stay markers. Play animates curated source-confirmed Beaver, Zinger and Klaptrap clips. Spawn conditions and scripts are not executed. **Fungi Forest night spawns** applies the three enemy substitutions from `807278C0` on map 48. Other spawn conditions and actor scripts remain unresolved.

**Map geometry is drawn at 1/3 scale**, as the map loader `func_global_asm_80650ECC` does with `guScale(1/3)`, so maps and placements share world units. Map GLB exports are in these world units.

The list records type, object ID and coordinates. Prop placement uses the scale → X → Y → Z → translation order from `8066C610`. Combined static GLB export includes the displayed content overlay. Placed prop texture frames advance with map playback; embedded part tracks remain explicitly selected in Other models.

## Rendering and data sources

Both decoders preserve ROM colour-combiner, cycle, primitive/environment, key and LOD state. The viewport evaluates one/two-cycle `(A-B)*C+D` RGB/alpha inputs, supports separate texture inputs/UVs for the generic decoder, and reads contiguous RGBA16 ROM mip levels. Lighting uses a fixed directional preview light, including shade-only clothing. Full RSP lighting, TMEM, coverage/blender/dither, fog and bit-exact LOD/rounding remain open. Standard glTF cannot express the complete RDP pipeline; exports keep the mux in metadata and use approximate PBR materials.

Geometry uses F3DEX2 vertex/texture/tile commands. Actor segment 3 points at vertices; segment 4 matrices select bone rest offsets and rigid joints. Prop segment 8 points at header `0x48`. Map chunks are 52-byte records from `0x68`, with vertices on segment 6 and shared sublists on segment 7.

**Chunk pieces.** A chunk's up to four display lists each hold one or more `G_ENDDL`-terminated pieces back to back, and every piece is drawn. Pieces that another piece calls through segment 7 are shared sub-lists; they are drawn through their caller only. Chunks address vertices in one of two ways:

- **Relative** (e.g. Funky's store): every piece starts at vertex 0 of its own block, and the blocks follow each other in display-list order.
- **Absolute** (e.g. Japes): all pieces index the whole chunk block.

Forge tells them apart structurally. Relative is detected when the piece extents add up exactly to the chunk's vertex size and no single piece reaches its end.

Treating relative chunks as absolute caused the dark, misjoined panels seen before 2026-10-03. Drawing only the first piece of each list hid about a third of all map geometry: 187k triangles before, 284k now across all maps.

The game binds a segment-6 pointer per piece through sub-records (`func_global_asm_80656B98`). The loader code that fills those pointers has not been traced, so the rule is structural evidence. Conditional Kong blocks use hand-state masks; other actors use the generic all-blocks mask.

Static images use table 25. Map and prop animation descriptors explicitly identify table-7 frames; this is traced loader behavior, replacing the previous size heuristic. Prop descriptors are at header `0x6C` with `0x84`-byte rows; map descriptors are at `0x48` with `0x7C`-byte rows. Prop blend flags enable fractional RGBA frame crossfades during playback. These are a desktop approximation of the RDP blend path.

Actor labels come from DK64 Randomizer (MIT/community); prop labels are ROM header strings; map names/layout facts come from the decompilation (CC0). Map layout facts from dk64_lib are used without copying GPL code. See [third-party attribution](../licenses/THIRD_PARTY.md) and [status/open work](status.md).

Further evidence and preview limitations: [Runtime preview](runtime-preview.md).

## Simplified controls

Animation dropdowns show a search field when they contain at least ten options. Short selectors (character, variant, view, timing, bank, filters and sorting) have no extra search field. Searches filter popup options without changing model or animation IDs. Clear the search to restore all choices. Models load with twice the previous camera distance; mouse-wheel zoom remains available.

The Characters panel hides reference bookmarks and technical animation details (Index, Samples, Domain, Timing, Ownership, Semantic, Prefix, Root, Loop and Context). Viewport Debug retains only View and the overall model geometry count. Source validation and diagnostic metadata remain available in exports and the documentation.

Level playback keeps geometry and placement buffers loaded. Animated texture pixels and effect-7 scrolling UVs update independently; repeated placement textures share GPU storage. The performance measurements are recorded in [status.md](status.md).

Other models and Levels request rendering about 60 times per second. Their animation clock remains 30 ticks per second and uses elapsed time, so a faster viewport does not speed up the animation. Repeated graphics state is cached within each mesh pass.

## Current-view workflow and responsiveness

Ctrl/Shift-select rows in the level object list to export only those objects at the current tick. Models and placement loading run in background jobs. The texture browser links directly to models/maps. Map fog and procedural effect 4 supplement effect 7; offscreen static batches are conservatively culled. See [viewer-expansion.md](viewer-expansion.md) for evidence, tests and remaining rendering limits.

Actor meshes start with baked vertex colours unless their display lists enable lighting explicitly. Treating those RGBA bytes as normals previously made enemies such as Klump appear white. Colours now survive both static and animated previews. Placement loading uses a separate cancellable worker from map loading, so toggling the content checkbox during selection cannot cancel the requested map.

Level picking, filters, optional interpolation, additional enemy routes and animated prop export are described in [level-exploration.md](level-exploration.md).
