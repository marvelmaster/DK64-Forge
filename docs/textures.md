# Textures

The **Textures** tab browses three banks, with search, usage filters, pixel preview and PNG export:

| Bank | Non-empty entries | Format evidence |
|---|---:|---|
| 25 — Geometry | 6,011 | Display-list usages and actor dynamic slots |
| 7 — Uncompressed | 993 | Map/prop animation descriptors and associated draw state |
| 14 — HUD | 167 | HUD draw usages are not yet traced; unknown entries remain undecoded |

The ROM stores raw texels without names or format headers. Unknown data cannot be decoded reliably from its byte length alone.

## Usage

Choose the bank, search a name/ID/user, and select a texture. Scroll the mouse wheel over the preview to zoom in or out; large previews have scrollbars. Selecting another texture resets the zoom. **Show** filters decoded, undecoded, used, unused and palettes; sorting uses number/name/size. The first visit scans actor, prop and map display lists.

The format, width, height, odd-row-swap and unreferenced-preview controls are hidden. Preview decoding uses known ROM draw usages; unknown entries remain undecoded. CI images need usage-derived palettes.

**Export PNG** saves the current selected decode at its original resolution. **Export shown list as PNG files** exports known usage-derived decodes; it does not apply one manual layout to the whole bank. Filenames include the bank, for example `T25_0E64_DK_14_32_32.png`, to avoid collisions.

Animation frame selection lives in the Characters and Other models/Levels panels. A frame in this browser is a separate bank entry, linked through its recorded users.

## Preview filtering

- **Default:** nearest-neighbour scaling preserves hard pixel edges; the initial scale uses an integer zoom.
- **Trilinear filtering:** the preview starts at 384 pixels on its longest side and follows mouse-wheel zoom. A mip chain of
  2×2 averages is built, the two levels around the scale factor are sampled bilinearly, and the
  result is blended between them. This is the same scheme as `GL_LINEAR_MIPMAP_LINEAR`.

This is a desktop preview filter. The N64 RDP itself uses three-point filtering and its own LOD
rules. Exports always write the decoded pixels unchanged.

## How formats are established

`G_SETTIMG` identifies the image; tile state gives format, dimensions, wrap and shift; texture loads give interleaving; `G_LOADTLUT` identifies CI palettes. Actor eye/mouth frames inherit the slot's usage. Map and prop animation frames inherit their associated draw usage and are recorded in table 7.

Static prop images are loaded from table 25; animated prop frames use table 7 (`80639CD0`, with descriptors read at `806349FC`). Maps bind table-7 frames to per-chunk dynamic segments (`8062EDA8`/`8062EE48`). The earlier size-based automatic table fallback has been removed.

Current scan: bank 25 has 3,947 used entries and 4,689 decodable entries including palettes; bank 7 has 557 used/decodable entries. These sets overlap and do not form a partition. Unreferenced data and HUD entries need further format evidence.

Names derive from the first actor/prop/map user and size; actor names are community labels from DK64 Randomizer, map names come from the decompilation, and prop names come from ROM headers. Treat these as labels rather than official texture names.

The Kong body texture `0xE64` remains pixel-identical to the reference decoder. I/IA previews are grey with alpha. RGBA32 odd-row swapping is not applied. Undecodable CI usages, exact TMEM/interleaving behavior, unused formats, HUD palettes and gameplay expression scripts remain open; see [status](status.md).

## Thumbnail and sequence browser

**Thumbnail grid** switches between thumbnails and the named list. Icons load in short batches so scrolling remains responsive; bank scans run in the background and are cached. Double-click a usage row under the preview to open its actor, prop or map.

When a ROM descriptor assigns a texture to a sequence, choose that sequence, click a frame in the strip or use **Play texture sequence**. IDs need not be consecutive. Actor-slot playback is a manual preview; map/prop playback uses descriptor timing at 30 ticks/s. The strip shows discrete frames. Unknown formats remain undecoded. See [viewer-expansion.md](viewer-expansion.md).
