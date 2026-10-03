# DK64 Forge

DK64 Forge is an experimental desktop viewer and exporter for Donkey Kong 64 US revision 0.

- **Characters:** the five playable Kongs (Donkey Kong, Diddy, Tiny, Chunky and Lanky) as textured,
  skinned models with their weapons and instruments; plays their animations and exports static or
  animated glTF, which opens in Blender.
- **Models, Levels, Audio and Textures:** browse every actor, prop and map, play the music and
  sound effects, and export textures.

![DK64 Forge showing Chunky Kong's walk animation](docs/screenshot.png)

*The screenshot shows the program's output for illustration.* **No ROM and no game data file is included.** You need your own legally obtained
Donkey Kong 64 US revision 0 ROM. It is read locally and never modified or
uploaded.

## Run it (Windows)

1. Install [Python 3.12 or newer](https://www.python.org/downloads/) (tick "Add python.exe to PATH").
2. Download this repository as a ZIP (green **Code** button → **Download ZIP**) and unpack it, or clone it:
   `git clone https://github.com/marvelmaster/DK64-Forge`
3. Double-click `start_forge.bat`. On the first run it creates a `.venv` and installs
   `PySide6`, `PyOpenGL`, `numpy` and `lameenc` (MP3 export) from `requirements.txt`; afterwards it just starts the app.
4. In the app choose **File → Load ROM** and select your Donkey Kong 64 US ROM
   (`.z64`, `.n64` or `.v64`; the file name does not matter, the content is validated).

Manual start from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m dk64_forge
```

The first time you open each Kong, the app scans the ROM's animation table and caches a small
result file in `local_output/` (local, ROM-derived, never uploaded).
Nothing else needs to be downloaded or set up.

## Using the app

- The character selector switches between the five Kongs. The animation selector lists the
  Table-11 animations that fit the character's skeleton; **Only … clips** hides everything the
  character's own animation code never plays. Labels such as `Walk` are derived from the game's
  code and are not official names.
- The **variant** selector under the character shows the Kong with its **weapon drawn** (Coconut
  Shooter, Peanut Popguns, Grape Shooter, Feather Bow, Pineapple Launcher) or its **instrument model**
  (all five Kongs, including DK with attached bongos), or the **low-poly model**. Weapons are part of each Kong model and follow the hand bones in every
  animation; the hand-state bits are the ones the game sets when the weapon is drawn.
- The viewport supports orbit, pan and zoom, mesh/skeleton views and animation playback.
- **Timing**: *Game rate* plays 30 adjusted units per second (observed for DK's Entry 4, a
  diagnostic assumption elsewhere), *Technical* plays one sample per second; the speed slider
  (0.1x–5.0x) scales both. **Mark Current as Reference** / **Go to Reference** jump back to a clip.
- **Viewport Debug** shows the selected joint's parent, current position, rest offset and the
  geometry it moves; the browser shows the root (bone 0) translation of the current frame.
- Start directly with a ROM: `.venv\Scripts\python.exe -m dk64_forge --rom C:\path\to\rom.z64`.
- **File → Export** writes the model, the current animation only (joints + clip, no mesh), or
  model plus animation as glTF. Keep the `.gltf`, `.bin` and any
  `textures/` folder together.
- **Export all**: every tab can export its whole list into a folder, with a progress dialog that
  can be cancelled.

  | Tab | What "Export all" writes |
  |---|---|
  | Characters (button, or File → Export) | the model plus every listed clip (model + animation) |
  | Models / Levels | every shown entry as a static GLB |
  | Audio | every shown song or sound as WAV |
  | Textures | every shown texture as PNG |

  Search and filters decide what is included. Entries that fail (e.g. undecodable textures) are
  listed at the end.
- **View menu**: these options apply to every 3D view.
  - **Show FPS Counter** (F3): frames painted per second and the render time per frame. A static
    view only repaints when something changes, so its fps is low while ms/frame shows the cost.
  - **Trilinear Texture Filtering**: mipmapped textures in the 3D views.
  - **Ground Grid**: the grid under the character preview, at y = 0 with the red X and blue Z axes;
    also on the "Show ground grid" checkbox.

| Kong | Bones | Faces | Animations in the browser | Played by the Kong | Labelled |
|---|---|---|---|---|---|
| Donkey Kong | 25 | 704 | 183 | 94 | 76 |
| Diddy Kong | 37 | 678 | 175 | 107 | 87 |
| Tiny Kong | 39 | 744 | 165 | 96 | 69 |
| Chunky Kong | 23 | 699 | 184 | 98 | 77 |
| Lanky Kong | 21 | 704 | 171 | 99 | 73 |

### Models and Levels tabs

The **Models** tab groups **Characters** and **Other models**. Other models browses actors and
props, supports compatible actor-clip playback/animated GLB export, and prop texture frames.
**Levels** adds map texture playback, placed setup props and actor/spawner meshes selected from the
game's model tables; entries without a model use markers.
The viewport evaluates ROM combiner state with preview lighting; full runtime fidelity remains open.
Details: [docs/models.md](docs/models.md).

### Audio tab

The **Audio** tab plays the 174 songs and 1,126 sound effects and exports WAV or MP3. Songs are rendered
from the game's compressed MIDI and instrument bank with the game's own reverb settings; sound effects
use the game's decoded samples (four stored ADPCM loop states remain unresolved). Details: [docs/audio.md](docs/audio.md).

### Textures tab

The **Textures** tab browses banks 25 (geometry), 7 (uncompressed) and 14 (HUD), with search,
usage-derived formats, manual decoding, pixel preview and bank-qualified PNG export.
**Trilinear filtering** switches the preview from exact texels at integer zoom to a smooth,
mipmapped scale to the panel size. It is a preview only; PNG exports keep the original pixels.
Details: [docs/textures.md](docs/textures.md).

## Project layout

- `dk64_forge/` – the application (window, viewport, export, ROM session, Models/Levels/Audio/Textures tabs).
- `dk64_forge/core/` – the DK64 core: ROM tables and decoding (`rom_model`), textures/hilite (`texgen`),
  skeleton, animation tables, census and labels, pose reconstruction and the Entry-4 reference clip,
  the texture bank (`texture_bank`), the generic static mesh decoder (`mesh_decoder`) and audio
  (`audio_rom`, `audio_sequence`, `audio_render`, `audio_reverb`, `audio_export`, `audio_names`).
  See `dk64_forge/core/__init__.py` for the module list.
- `start_forge.bat`, `requirements.txt` – launcher and dependencies.

## Project status and open work

The JFG feature-parity milestone is complete. See [status and open work](docs/status.md) for the supported features, remaining tasks and evidence limits. The content/material expansion is implemented, with runtime-fidelity gaps recorded explicitly.

## Limitations

Experimental research tool. Preview lighting is a fixed key light; full N64 TMEM, blender/fog and
rounding are not reproduced. Generic actor clips establish structural compatibility rather than
ownership; prop skeletal animation remains open. Eye/mouth frames are selectable but gameplay
scripts are not simulated. Tiny has an optional source-derived hair diagnostic with approximate
world/anchor inputs. Most animation timing uses a diagnostic 30 units/s rate, and ordinary runtime
adjustments/world placement remain omitted. View-dependent highlight exports use a fixed camera.
Level actors use their assigned rest-pose meshes; missing models and unsupported props use markers.
Spawn simulation and chunk visibility remain open.
Songs omit chorus and mid-note pitch bends; SFX use their samples' own pitch and volume.

## Legal and license

The source code is released under the [MIT License](LICENSE). Donkey Kong 64 and its
characters belong to their respective owners; this is an unofficial, non-commercial research
tool. The viewport layout and the audio player/renderer are adapted from JFG Forge
([license](licenses/JFG_Forge_MIT.txt)); other sources are listed in [licenses/THIRD_PARTY.md](licenses/THIRD_PARTY.md).
