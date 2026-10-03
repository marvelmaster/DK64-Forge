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

The ROM stores no audio names.

- **Song names** come from the DK64 Randomizer song list (MIT, COMMUNITY), e.g. `Jungle Japes (Starting Area)`. Its `mem_idx` is the table-0 index. The decompilation's `MUSIC_E` enum is the fallback.
- **Sound effects** keep their number. Only the arcade sounds have names, from the decompilation's `SFX_E` enum.

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
- A sound-effect number is an index into the sound bank's single instrument (LIKELY). The arcade code plays its `SFX_E` numbers this way.

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
