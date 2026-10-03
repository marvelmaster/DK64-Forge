# Runtime preview additions

The viewer now supports additional source-driven animation and level rendering. It does not execute the game engine. Source selection and binary layouts are verified against the local US revision-0 ROM/decompilation; timing and camera/gameplay inputs have the limits below.

| Feature | Evidence | Preview behavior and limits |
|---|---|---|
| Prop moving parts | `code_54150.c`, ROM disassembly `8064F450`, `8064F97C`, `8064FB64`, `806500E0` | Header 64/68 tracks and matrix buffers, stored pre/post transforms, shortest-angle TRS interpolation; segment-9 matrices transform vertices and normals. 348 prop entries have matrix sections. Choose a track and script speed explicitly; forward looping is diagnostic. Prop animation export is still open. |
| DK bongos | Actor 239 dispatch table → `8069E040` → Table-13 script 299 → clips 494/495/493; `806F10E8` | Separate three-bone actor at scale 1.25 follows DK's root and the selected clip clock. Exports include its skin and channels. Clip interior samples are concatenated at 30 units/s; script waits, speed changes and endpoint transitions remain unresolved. |
| Kong blinking | `806CF580`, `8072881C`, dynamic-slot updater `80687FC8` | One eye slot for DK/Lanky/Chunky, two synchronized slots for Diddy/Tiny. Rate 1, three frames, ping-pong, two crossings; 4% trigger after 50 ticks. Fixed preview RNG seed enables repeatable scrubbing. Rocketbarrel-specific scripts and mouth/expression changes remain open. |
| Dynamic actor slot parsing | `80687D50`, `80687EE0` | Third header field is an enable flag, not a frame multiplier. Disabled slots consume their image records but do not bind a texture segment. |
| Prop frame blending | `80639CD0`, `80639F1C` | Blend-marked descriptors crossfade adjacent RGBA images. Preview uses floating-point pixel interpolation; exact RDP rounding and blend scheduling remain open. |
| Billboard props | `8063524C`, `80635468` | All 70 type-2 entries decode their quad XYZ, S10.5 UV, texture dimensions/format and bank. Cylindrical camera-facing preview; static exports retain fixed quads and billboard metadata. Per-object game camera transforms/lighting are not reproduced exactly. |
| Scrolling map surfaces | `8062CEA8`, `8063D638`, `8063D854` | Map-tree effect-7 display lists now render with source texture 1765, combiner, transparency and scroll/reset rules. Mask-derived dimensions are preserved while scrolling the tile origin. Other procedural effect IDs and dynamic transforms remain open. |
| Chunk inspection | ROM map display-list chunk records | All geometry or one geometry chunk. This is an inspection control; the game's portal/visibility algorithm is not executed. |
| Fungi night spawns | `807278C0` and ROM table 80755698 | Enemy values 28→63, 9→54, 44→67 (hex destination IDs), under the source's class-table condition on map 48. Other spawn conditions remain open. |
| Placed content | `8066C610` | Source prop rotation order; placed texture frames advance during map playback. Actors keep their initial poses unless inspected separately in Other models. |

Use [Models and Levels](models.md) for controls. [Project status](status.md) retains the remaining work, including gameplay expression/actor scripts and exact runtime comparisons. ROM-derived fixtures, screenshots and regression tests stay local under the existing ignore rules.
