# The Models and Levels tabs

The **Models** tab shows every actor model (pointer table 5) and every model-two prop (table 4).
The **Levels** tab shows the map geometry (table 1).
Every entry is drawn as a static, textured mesh with its vertex colours. **Export static GLB...** writes it as one binary glTF, which opens in Blender.

| | Entries shown | Triangles (all entries) | Textured |
|---|---:|---:|---:|
| Actors (table 5) | 217 | ~55,000 | ~97 % |
| Props (table 4) | 581 (70 without triangles) | ~33,000 | ~96 % |
| Maps (table 1) | 136 (85 stubs skipped) | ~165,000+ | ~99 % |

## Using it

- **Search** by name or by number, in decimal or `0x` hex. In the Models tab, the selector limits the list to actors or props.
- The info panel shows:
  - where the name comes from;
  - the triangle and batch counts;
  - how many images were decoded and what share of the triangles is textured;
  - display-list commands the static decoder skipped.
- Magenta surfaces reference a texture that could not be decoded.
- Grey surfaces are untextured.

## How the geometry is read

All three kinds use one F3DEX2 decoder, `dk64_forge/core/mesh_decoder.py`.

- **Vertices:**
  - Each vertex is 16 bytes: position, S/T, then a normal or an RGBA colour.
  - When `G_LIGHTING` is clear, the four bytes are the vertex colour. Forge multiplies the texture by this colour, the usual `TEXEL0 × SHADE` combiner.
  - When lighting is on, Forge uses a fixed key light for the preview. This is not game lighting.
- **UVs and textures:** these follow the verified Kong rules:
  - `G_TEXTURE` scale;
  - tile shift and the tile's upper-left corner;
  - the bilerp texel centre;
  - `dxt = 0` interleaving.
  
  Textures are decoded with the texture bank.
- **Props (table 4):**
  - The header range `0x40..0x48` holds several display lists back to back: a setup list, then the geometry, each ended by `G_ENDDL`.
  - `G_VTX` segment 8 is the vertex block at `0x48` (DK64 Randomizer `model_port.py`, MIT).
  - Props that animate parts with `G_MTX` are shown in their rest layout.
- **Maps (table 1):**
  - Maps are split into chunks of 52 bytes each, from header offset `0x68`.
  - Each chunk has up to four display lists, relative to the display-list start at `0x34`.
  - Each chunk also has its own vertex offset, which becomes `G_VTX` segment 6 inside the vertex data at `0x38`. These layout facts come from dk64_lib (GPL); Forge uses the facts only, no code.
  - Shared sub-lists are called through `G_DL` segment 7, which is the display-list start. This is VERIFIED structurally: every target starts right after a `G_ENDDL`.
  - Maps whose entry is a pointer stub are skipped.
- **Actors (table 5):** these use the same parsing as the Kongs:
  - vertex segment 3;
  - `G_MTX` segment 4, which selects the bone rest offset;
  - the first frame of each dynamic texture slot.
  
  Conditional blocks (`G_DL` segment 7/8 branch up to a marker) follow `actor->unk146`. The playable Kongs use their hand mask, so DK gives the same 704 triangles as the verified Kong decoder. Other actors use the generic spawn default of `-1`, which draws every block.

## Texture table for props (DIAGNOSTIC ASSUMPTION)

Display lists name images by index.

- For maps and actors, the index points into table 25.
- About 100 prop usages only fit **table 7** (uncompressed textures) exactly, while their table-25 entry is too small. An example is the torch flame: 16×64 RGBA32 frames `0x8C`–`0x97`, which the prop also lists as a texture animation.

Forge therefore uses table 25 when its entry is large enough, and otherwise table 7. The runtime selection logic is not traced yet.

## Limits

- Static rest pose only. There is no animation for props or non-Kong actors, and no map texture animation, water scrolling or the like.
- The colour combiner, fog, lighting and 2-cycle modes are approximated.
- Maps are shown as their whole geometry. Chunk visibility, map-object placement (model-two setup) and actor spawns are not applied.
- Names are labels:
  - actors come from the DK64 Randomizer model list (COMMUNITY);
  - maps come from the decompilation's map enum;
  - props use the ASCII name stored in their own header, which several props share (e.g. `torches`).
