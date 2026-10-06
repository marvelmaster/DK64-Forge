# The Audio tab

The **Audio** tab plays DK64's music and sound effects and exports them as WAV or MP3.
It has two parts, **Music** and **Sounds**. Both share one player, so only one clip plays at a time.

## Music

Pointer table 0 holds 175 songs. One is empty, which leaves **174 songs**. Each row shows the song number, its name, its length and its number of notes.

- **Play** renders the song (about a second for most songs) and plays it.
- Songs play **once through**. Most game songs end in an endless loop; Forge plays the intro and one pass of the loop.
- Details show:
  - the name and where it comes from;
  - the song type (BGM, event, ...);
  - the tempo and the number of instruments.

## Sounds

The sound bank has **1,126 sound effects**. **Play when selected** is on by default, so you can step through the list with the arrow keys. **Only looping sounds** shows the effects that loop; Forge repeats them for three seconds.

## Names

The ROM stores no audio names. Song names come from the DK64 Randomizer song list
(MIT, COMMUNITY), with the decompilation's `MUSIC_E` enum as fallback.

### Sound-name research (2026-10-07)

Forge now has descriptions for **920 of 1,126 playable sound IDs**; **206 remain
unnamed**. ID 0 is the no-sound sentinel and is excluded from those totals.
Compared with the previous local catalog (199 labelled IDs including zero),
**746 previously unlabelled IDs now have sourced descriptions**, representing
**671 distinct decoded samples**. This is not a claim to have recovered official
Rare titles or independently identified every speaker.

The new source is the hand-renamed WAV archive linked in the **2024-10-17 comment**
on [The Sounds Resource's DK64 sound collection](https://sounds.spriters-resource.com/nintendo_64/donkeykong64/asset/402582/):
[community archive](https://files.catbox.moe/dobjw9.zip).
The source describes its own naming as informal. Forge preserves every accepted
source filename and marks its wording and speaker attribution as community
information, not independently verified. Display formatting only separates
existing prefixes and variant numbers; it does not invent trigger names.

Results of matching the entire archive:

- **981 WAVs**: all match decoded ROM PCM byte-for-byte (SHA-256), with zero
  unmatched files. Matching uses signed 16-bit mono sample values, not filenames,
  guessed ordering, sample duration, or perceptual similarity.
- **912 sound IDs / 809 distinct samples** accepted into `dk64_forge/data/sound_names.json`.
- **206 bank entries** excluded from this import because identical PCM has multiple
  different filenames. Forge does not select an arbitrary name for these cases.
- **8 further bank entries** excluded for unreliable source wording (jokes, an empty
  description, or an unsupported actor-credit claim). All decisions are recorded.
- Multiple sound IDs may share one sample, while envelopes, pitch, WAV sample rate,
  and playback context differ. A matched name describes the sample, not necessarily
  the full in-game effect or its trigger.

Existing decompilation enum labels and Randomizer symbolic names take priority,
followed by the 45 retained source-context descriptions, then the matched archive
names. The name-source panel includes the archive member and matching method.
Earlier generic additions such as `Actor action cue` were removed from the name
catalog. Earlier waveform aliases were also removed: they had been computed with
an incorrect game-ID offset and did not substantiate their named associations.
The old unverified `Doink` ID claim was removed rather than carried forward.

### Correct sound-ID mapping

The game uses **IDs 1..1126**, which access bank indices **0..1125**. ID **0** means
no sound. This is explicit in decompilation
`src/global_asm/audio/n_sndplayer.c`, `func_global_asm_80737638`:
`soundArray[soundnum - 1]` inside the `soundnum != 0` branch.
Forge previously treated game IDs as zero-based bank indices, causing names to
refer to the next sample. Playback now uses the correct mapping, including the
last sample and the non-playable zero entry. Existing exported files are not
renamed; their old numeric IDs may reflect the previous offset.

Independent anchors agree: `Okay` is game ID **572 / 0x23C**, bank index **571 /
0x23B**, and matches `dkokay.wav`; `Get out` is game ID **418 / 0x1A2**, bank
index **417 / 0x1A1**, and matches `aztecgetout.wav`.

### Reproduce and verify

The source archive, PCM caches and ROM remain local and are not distributed.
Only names, provenance and fingerprints are included in the repository.

```powershell
python -m tools.build_sound_names local/roms/dk64_us.n64 local_output/sound-research/community.zip local_output/sound-research/rebuilt_names.json
python -m unittest tools.test_sound_names
```

The builder reads WAV members without extracting the ZIP. It records the archive
and normalized-ROM SHA-256, original filename, PCM SHA-256, source sample count,
source WAV rate, bank index and game ID. Rejected groups are retained for future
review. The catalog is bundled metadata; browsing sounds requires no network or
archive access.

Tests check every imported entry against both its source WAV and the ROM, unique
IDs, exclusions, the known anchors, first/last sound playback and the zero sentinel.
The local archive/ROM tests skip explicitly when their inputs are unavailable.

### Startup regression and validation

A follow-up import-order edit briefly prevented Forge from starting:
`SyntaxError: from __future__ imports must occur at the beginning of the file`.
`audio_names.py` now places `from __future__ import annotations` immediately
below its module docstring, before standard-library imports. The complete
`dk64_forge.ui` import was verified after the fix, in addition to the five
catalog checks and six existing audio tests (all eleven passed).
Catalog regeneration from the local ROM/archive also produced a byte-identical
JSON file. A final focused run of catalog, audio, texture-assembly and workspace tests
passed all 41 checks in 30.510 seconds, with Qt using its offscreen platform.
These results verify imports, data and the tested UI/session paths, not a full
interactive playthrough of the application.

## Export

**Export WAV...** and **Export MP3...** save the selected entry.

| | Format |
|---|---|
| Songs | Stereo, 32,000 Hz, with the game's reverb, scaled to a non-clipping peak |
| Sound effects | Mono, at the bank's 22,050 Hz |

MP3 export uses the `lameenc` package from `requirements.txt`. The audio belongs to its rights holders; keep exports for your own use.

## Where the data comes from

The ROM layout is VERIFIED from the decompilation (CC0):

| ROM range | Content |
|---|---|
| `0x188AF20`–`0x1897860` | Music bank control (`.ctl`) |
| `0x1897860`–`0x1A97280` | Music samples (`.tbl`) |
| `0x1A97280`–`0x1ABCBF0` | Sound-effect bank control |
| `0x1ABCBF0`–`0x1FED020` | Sound-effect samples |

- The decompilation shows where the banks are loaded: `dk64_boot_1050.c` fills `gOverlayTable`, and `func_global_asm_80600D50` calls `alBnkfNew` for both banks.
- The control files use the loader's **mode-2 compression**, a bit-level LZSS with a 13-bit window. It was read directly from the ROM's own MIPS code (`func_dk64_boot_800028E0`). Both decompress to a standard libultra `ALBankFile`:
  - music: 95 instruments, 303 sounds;
  - effects: 1 instrument, 1,126 sounds.
- **Samples** are VADPCM. The decoder reproduces 176 of the 180 loop states stored in the ROM exactly. The other 4 belong to three sound-effect samples.
- **Songs** are libultra compressed MIDI (`CSeq`). All 165,560 notes use instruments that exist in the bank.
- **Reverb**: the game configures two custom effect buses. The settings are read from the ROM:
  - `func_global_asm_80601A10` was read from the ROM's MIPS code. It copies a parameter table from global_asm `.data` (`0x807452D0`) into the synthesizer config.
  - **Bus 0** is a room reverb: 8 sections over an 8,816-sample delay line. The last section is a silent chorus.
  - **Bus 1** is an echo: 2,200 samples, feedback 0.4.
  - Songs never select a bus (controller 92), so they use bus 0.
  - Each channel's controller 91 splits its sound between dry and effect with the library's equal-power curve.
  - At boot the game selects the **stereo** effect (`func_global_asm_80737C20(4)` and `func_global_asm_80737CF4(0, 4)`): one delay line per side. The left result goes to the left output at 0.7071 (`n_alFxPull`), the right result to the right output at 1.0 (`func_global_asm_8073FD90`).
  - The mono variant (sound mode 1) is implemented as well. It sums both sides at 0.5 and scales the section gains by 1.4142.
- A sound-effect number is one-based: game ID `n` reads bank index `n - 1`; zero means no sound (verified in `func_global_asm_80737638`).

The sequence decoder, the song renderer, the export and this tab's layout are adapted from JFG Forge (MIT). Jet Force Gemini uses the same libultra audio formats.

## How close to the game is it?

See [status and open work](status.md) for the consolidated backlog. Sample decoding matches 176 of 180 stored ADPCM loop states; four states belonging to three SFX samples still differ, with the cause unknown.

- **Sound effects** use the game's own VADPCM samples at their sample's own pitch and volume, subject to the four unresolved loop-state mismatches above. DK64 has no per-effect pitch/volume table like Jet Force Gemini; the game sets these per call.
- **Songs** are rendered by Forge. It follows:
  - notes, tempo and instrument key maps;
  - pitch, envelopes and loops;
  - channel volume and pan.
  
  It also applies the game's own reverb with the settings and mixing described above. The delay line is processed in the console's 184-sample steps at the console's 22,050 Hz timing, scaled to 32,000 Hz.
  
  **Left out:**
  - chorus (the only chorus section is silent);
  - sustain and mid-note pitch bends;
  - the flag in bit 7 of a channel's effect send (controller 65), whose effect in the RSP is unknown;
  - the console's fixed-point rounding.
  
  The sound-mode setting in the game's options can switch the reverb to mono. Forge renders the boot default (stereo).
