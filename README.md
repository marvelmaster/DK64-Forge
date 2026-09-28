# DK64 Forge

DK64 Forge is an experimental desktop viewer and glTF exporter for the five
playable Kongs (Donkey Kong, Diddy, Tiny, Chunky and Lanky) of Donkey Kong 64
US revision 0. It shows the textured, skinned model, plays the character's
animations and exports static or animated glTF (opens in Blender).

**No ROM and no game asset is included.** You need your own legally obtained
Donkey Kong 64 US revision 0 ROM. It is read locally and never modified or
uploaded.

## Run it (Windows)

1. Install [Python 3.12 or newer](https://www.python.org/downloads/) (tick "Add python.exe to PATH").
2. Download or clone this repository.
3. Double-click `start_forge.bat`. On the first run it creates a `.venv` and installs
   `PySide6`, `PyOpenGL` and `numpy` from `requirements.txt`; afterwards it just starts the app.
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
- The viewport supports orbit, pan and zoom, mesh/skeleton views and animation playback.
- **File → Export** writes static or animated glTF. Keep the `.gltf`, `.bin` and any
  `textures/` folder together.

| Kong | Bones | Faces | Animations in the browser | Played by the Kong | Labelled |
|---|---|---|---|---|---|
| Donkey Kong | 25 | 704 | 183 | 94 | 76 |
| Diddy Kong | 37 | 678 | 175 | 103 | 83 |
| Tiny Kong | 39 | 744 | 165 | 95 | 68 |
| Chunky Kong | 23 | 699 | 184 | 98 | 76 |
| Lanky Kong | 21 | 704 | 171 | 92 | 66 |

## Project layout

- `dk64_forge/` – the application (window, viewport, export, ROM session).
- `dk64_forge/core/` – the DK64 core: ROM tables and decoding (`rom_model`), textures/hilite (`texgen`),
  skeleton, animation tables, census and labels, pose reconstruction and the Entry-4 reference clip.
  See `dk64_forge/core/__init__.py` for the module list.
- `start_forge.bat`, `requirements.txt` – launcher and dependencies.

## Limitations

Experimental research tool, not a runtime-faithful reproduction. Only the normal playable
models are supported (no instrument or low-poly variants, no other actors). Lighting is not
modelled (shirts and shoes render flat white), animation timing is a browser convenience
(30 units/s) for all clips except the reference clip of Donkey Kong, and adjustment rows and
world placement are omitted. The exported textures use a fixed front camera for the
view-dependent highlight faces.

## Legal and license

The source code is released under the [MIT License](LICENSE). Donkey Kong 64 and its
characters belong to their respective owners; this is an unofficial, non-commercial research
tool. The viewport layout is modelled on JFG Forge ([license](licenses/JFG_Forge_MIT.txt)).
