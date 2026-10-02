# The Textures tab

The **Textures** tab is a browsable bank of the ROM's geometry textures (pointer table 25, 6,011 non-empty entries). It shows a pixel preview of each texture with its format and size, lists the actors, props and maps that use it, and exports PNG files.

## Using it

- **Search** by name, by number (decimal or `0x` hex) or by a user, e.g. `japes` or `torches`.
- **Show** lets you filter: all textures, decoded or not decoded, used by a model or map, unused (no reference found), or palettes.
- **Sort by** number, name or byte size.
- Click a texture to see it at an integer zoom.
- **Export PNG...** saves the selected texture. **Export shown list as PNG files...** saves every decodable texture in the current filter. Files are named like `T0E64_DK_14_32_32.png`.
- The tab scans the ROM the first time you open it (a few seconds).

## Where formats and names come from

**The ROM stores raw texels only.** Each texture has no header, format, size or name. Forge therefore reads every display list of the 236 actor models (table 5), the model-two props (table 4) and the maps (table 1), and records each time a texture is loaded:

- `G_SETTIMG` gives the image, as segment 0 plus a table-25 index.
- `G_SETTILE` gives the format, and `G_SETTILESIZE` the width and height of the render tile.
- `G_LOADBLOCK` gives the texel count and `dxt`. With `dxt = 0` the image is stored pre-interleaved in RAM; Forge undoes this on odd rows, as verified for the Kongs.
- `G_LOADTLUT` marks the palette of CI images.

The dynamic texture slots of actors (eyes, mouths) give their frames the slot's usage.

When users load a texture in different ways, the most common way is shown and the info panel says so.

**Names are derived:** from the first user, then `+N` for the other users, then the size, e.g. `Japes Mountain +3 · 32×32`.

- Actor names come from the DK64 Randomizer model list (COMMUNITY, MIT).
- Map names come from the decompilation's map enum (CC0).
- Prop names are stored in the ROM itself, at the model-two header offset `0x0C` (e.g. `torches`).

Treat all names as labels, not the game's own names.

## Decoding and limits

| Status | Count | Meaning |
|---|---:|---|
| Used and decoded | ~3,820 | Format from usage. Covers RGBA16, RGBA32, CI4/CI8 (RGBA16 palette), IA4/IA8/IA16 and I4/I8 |
| Used, not decoded | ~165 | CI images without a full palette found by the scan, entries smaller than their usage, or invalid format/size pairs |
| Palettes | ~811 | 16- or 256-colour RGBA16 tables, shown as a colour strip |
| Unused | ~1,212 | No actor, prop or map loads them; e.g. map texture animations and images used by game code |

- The decoders were checked visually per format. The Kong body texture `0xE64` is pixel-identical to the verified Kong decoder.
- I and IA images are shown as grey with their alpha. RGBA32 interleaving is not undone (same choice as JFG Forge).
- **Unused** data has no known format. The **GUESS** checkbox previews it as RGBA16 with 32 texels per row; that is a guess, not evidence.
- Not covered yet: tables 7 (uncompressed textures, 993 entries) and 14 (HUD, 167 entries), map texture animation frames, and a per-texture frame slider.
