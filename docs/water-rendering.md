# Map water rendering

Implemented 2026-10-07, using the supported DK64 US revision 0 ROM and the
CC0 decompilation's `src/global_asm/code_63EC0.c`. No ROM assets are included.

## Using the preview

Water loads automatically with Levels geometry. **Play** advances the waves and
texture layers at the existing 30-tick preview clock; **Pause** freezes them.
The placed-objects checkbox does not control water. Current-view and all-shown
GLB exports include the displayed water geometry, vertex colours and UV state.
GLB freezes a surface; it does not export a running water simulation. Standard
glTF approximates the two-texture RDP combiner and retains its mux metadata.

Material types 0, 3 and 6 are supported: 87 records across 20 maps in the local
ROM audit. Other procedural materials remain explicitly listed in the map's
Notes rather than substituted with arbitrary water planes.

## Source evidence

Water is separate from ordinary terrain display lists and from the procedural
effect tree at map header +0x30. Map 007 Japes has no effect-tree entries.

| Routine/address | Evidence |
|---|---|
| `8065F1C0` | Loader reads map header +0x4C, then a big-endian u32 count and 0x6C-byte records. Its MIPS code increments the record pointer by 0x6C. |
| `80660070` | Generates grid triangles `(right, left, below)` and `(below, below-right, right)`. |
| `80660D38` | Builds source X/Z and UVs; clamps the final grid row/column to the bounds; multiplies positions by 3 for the map matrix. |
| `80660520`, `806608FC` | Calculate vertex height and alpha. Ordinary waves use two position-dependent sine terms. |
| `80612794`, `8061ACA0` | The sine lookup uses the ROM quarter-wave table with odd-phase half-step interpolation. |
| `80748A90` | Material dispatch table: initializer, renderer, updater and two texture pointers, stride 0x18. |
| `80661658` | Types 0 and 3 load table-7 texture **03C5**, a 32×32 RGBA32 image. A texture-ID assumption from another renderer is unnecessary. |
| `806616A0` | Type 0 uses two-cycle multiplication of two texture samples and vertex shade/alpha. Tile shifts 14 and 13 multiply texture coordinates by four and eight. |
| `806618A0` | Type 3 uses the same samples; alpha comes from the record's primitive alpha. |
| `80661AB4`, `80661E34` | Advance wave phases and texture origins; float32 subtraction resets a negative origin to 255, rather than wrapping by 256. |
| `806625D0`, `80662618` | Type 6 loads table-7 texture **03D2**, RGBA16, with a single scrolling tile. |

The loader and several geometry routines are still `GLOBAL_ASM` in the local
decompilation. Their offsets, grid generation, UV scale and sine calculations
were read from the ROM's decompressed MIPS code, not inferred from screenshots.
Capstone was used only as a local research tool; it is not an app dependency.

## Record layout used

| Offset | Meaning |
|---|---|
| +00 | Texture-coordinate scale |
| +04, +08 | X/Z wave spatial frequencies |
| +0C, +10 | X/Z vertical wave amplitudes |
| +14, +18 | Integer vertical wave phase increments |
| +1C, +20 | X/Z horizontal wave spatial frequencies |
| +24, +28 | Horizontal displacement amplitudes |
| +2C, +30 | Integer horizontal wave phase increments |
| +34, +38 | Texture-origin scroll speeds |
| +3C, +40 | Initial texture origins |
| +44 | Signed grid spacing |
| +46, +48, +4A, +4C | Signed X minimum, Z minimum, X maximum, Z maximum |
| +4E | Signed base height, in world units |
| +50 | Up to eight signed chunk references |
| +61…+63 | Vertex RGB |
| +64, +65 | Base alpha and alpha modulation amplitude |
| +66, +67 | Material type and chunk-reference count |

Grid spacing and bounds are world units. Generated source vertices use triple
coordinates and the map's 1/3 matrix; Forge stores the resulting world positions.
Degenerate repeated boundary triangles are omitted. Pixel data comes unchanged
from the ROM; texture coordinates include the native 0xFFFF texture scale,
S10.5 units, tile shifts and quarter-texel origins.

## Japes validation

Japes stores five type-0 surfaces:

| Surface | X bounds | Z bounds | Base height |
|---|---|---|---|
| 0 | 1003–1303 | 1400–1900 | 260 |
| 1 | 1303–1403 | 1500–2000 | 260 |
| 2 | 1903–2403 | 1500–2200 | 260 |
| 3 | 1403–1903 | 1500–2200 | 260 |
| 4 | 189–889 | 2569–3269 | 204 |

All five use grid spacing 100, vertical amplitudes 7/4, phase increments 28/38,
scroll speeds 0.3/1.0, RGB 255/255/150, base alpha 180 and alpha amplitude 50.
They add 278 nondegenerate triangles to the existing 6,194 terrain triangles,
with no missing textures. Native OpenGL captures of the river and pool were
visually inspected, both paused and playing. GPU texture handles stayed unchanged.

Cached playback updates water vertices, shade and UVs without decoding terrain
again. The viewport uploads only the affected spans; Japes has one span of 834
vertices. A local 120-tick CPU-only sample measured median 2.29 ms and p95 2.84 ms.
A separate 60-frame native 1200×800 logical-pixel capture measured median 17.47 ms
for playback, upload and event processing/drawing combined. These are local probe
measurements, not a guaranteed frame rate on every machine.

`python -m unittest tools.test_map_water -v` covers malformed sections, bounds,
triangle orientation, fractional scroll reset, source texture/height/material,
cached-vs-fresh parity at six ticks, untouched terrain, chunk filtering,
additional levels, partial viewport uploads and current-pose GLB content.
All 20 maps containing supported surfaces loaded and sampled successfully.
The final pre-push regression run passed all **35 checks in 34.783 seconds** across
water, placement reloads/the full-map audit, level selection, animation switching,
ROM-dialog and workspace suites, with no skips. UI import passed.

## Remaining limits

Material types 1, 2, 4, 5, 7 and 8 are not implemented by this water path.
They include other procedural surface variants; the entire section is not
assumed to be water. There are 40 such records in the local ROM census.

Game-triggered height changes, player/actor ripple impulses, portal/visibility
scissor rectangles and runtime map progression are not executed. The preview
uses the authored initial heights and ordinary waves. A cutaway view can expose
the original rectangular water bounds beyond the surrounding walls; Forge does
not invent a shoreline mesh to hide them. Existing desktop RDP lighting, fog,
coverage, blending and rounding limitations still apply.
