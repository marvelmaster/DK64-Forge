"""DK64 audio in the ROM: two libultra sound banks and the compressed MIDI songs.

Layout (VERIFIED from the decompilation, CC0: dk64_boot_1050.c fills gOverlayTable and
code_5A50.c ``func_global_asm_80600D50`` loads the banks with ``alBnkfNew``):

=========================  =====================  =====================================
ROM range (US rev 0)       content                used for
=========================  =====================  =====================================
0x188AF20 .. 0x1897860     bank 1 control (.ctl)  music: the four sequence players
0x1897860 .. 0x1A97280     bank 1 samples (.tbl)
0x1A97280 .. 0x1ABCBF0     bank 2 control (.ctl)  sound effects (sound player)
0x1ABCBF0 .. 0x1FED020     bank 2 samples (.tbl)
pointer table 0            175 songs, libultra compressed MIDI ("CSeq")
=========================  =====================  =====================================

The control files are compressed with the loader's mode 2 (``func_dk64_boot_80002790``
-> ``func_dk64_boot_800028E0``, read from the ROM's own MIPS code): a bit-level LZSS with
a 13-bit window (see :func:`lzss_decompress`). Both decompress to a standard single-bank
``ALBankFile`` ('B1'); the samples are VADPCM and stay uncompressed in the ROM.

A sound effect number is the index into the sound bank's single instrument (1,126
sounds); the arcade sound effects named in the decompilation's ``SFX_E`` enum match this
(LIKELY for the rest). The ROM has no per-effect pitch/volume index like Jet Force Gemini,
so effects play at their sample's own pitch and volume.

Bank parsing and the VADPCM decoder are adapted from MIT-licensed
jfg_forge.core.audio_rom (Copyright (c) 2026 Marvelmaster).
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

import numpy as np

from . import texture_bank

DEFAULT_OUTPUT_RATE = 32000
MUSIC_CTL = (0x188AF20, 0x1897860)
MUSIC_TBL = (0x1897860, 0x1A97280)
SFX_CTL = (0x1A97280, 0x1ABCBF0)
SFX_TBL = (0x1ABCBF0, 0x1FED020)
SONG_TABLE = 0
LZSS_INDEX_BITS = 13  # the loader's fourth argument (0xD) for both control files


class AudioError(ValueError):
    """The audio data does not have the expected layout."""


@dataclass(frozen=True)
class Envelope:
    attack_us: int
    decay_us: int
    release_us: int
    attack_volume: int
    decay_volume: int


@dataclass(frozen=True)
class KeyMap:
    velocity_min: int
    velocity_max: int
    key_min: int
    key_max: int
    key_base: int
    detune_cents: int


@dataclass(frozen=True)
class AdpcmLoop:
    start: int
    end: int
    count: int
    state: tuple[int, ...]


@dataclass(frozen=True)
class WaveTable:
    base: int
    length: int
    type: int
    flags: int
    order: int
    predictors: int
    book: tuple[int, ...]
    loop: AdpcmLoop | None


@dataclass(frozen=True)
class Sound:
    envelope: Envelope
    key_map: KeyMap
    wave: WaveTable
    pan: int
    volume: int


@dataclass(frozen=True)
class Instrument:
    volume: int
    pan: int
    priority: int
    bend_range: int
    sounds: tuple[Sound, ...]


@dataclass(frozen=True)
class SoundBank:
    sample_rate: int
    instruments: tuple[Instrument, ...]
    samples: bytes = b""

    @property
    def sound_count(self) -> int:
        return sum(len(instrument.sounds) for instrument in self.instruments)


@dataclass(frozen=True)
class SoundEffect:
    """One playable sound effect (game sound number)."""

    sound_id: int
    bite: int          # index of the sample-bank sound this effect plays
    volume: int = 128  # 128 = full scale (no per-effect table in DK64)
    min_volume: int = 0
    pitch: int = 100   # 100 = original pitch
    range: int = 0
    priority: int = 0


@dataclass(frozen=True)
class Song:
    song_id: int
    volume: int
    tempo: int
    reverb: int
    data: bytes


@dataclass(frozen=True)
class AudioRom:
    music_bank: SoundBank
    sfx_bank: SoundBank
    effects: tuple[SoundEffect, ...]
    songs: tuple[Song, ...]
    reverb: object | None = None  # bus-0 effect settings (audio_reverb.ReverbSettings)
    echo: object | None = None    # bus-1 effect settings; no song selects this bus


def lzss_decompress(source: bytes, index_bits: int = LZSS_INDEX_BITS) -> bytes:
    """Mode-2 loader decompression, as in ``func_dk64_boot_800028E0``.

    MSB-first bit stream. Flag 1: an 8-bit literal. Flag 0: an ``index_bits`` window
    position (0 ends the stream), then a ``16 - index_bits`` bit count; ``count + 3`` bytes
    are copied from the ring window. Every output byte also goes into the window, whose
    write position starts at 1.
    """
    length_bits = 16 - index_bits
    mask = (1 << index_bits) - 1
    window = bytearray(1 << index_bits)
    out = bytearray()
    position = 1
    total = len(source) * 8
    bits = format(int.from_bytes(source, "big"), f"0{total}b") if source else ""
    bit = 0

    def take(count: int) -> int:
        nonlocal bit
        bit += count
        return int(bits[bit - count:bit], 2)

    while bit < total:
        if take(1):
            value = take(8)
            out.append(value)
            window[position] = value
            position = (position + 1) & mask
            continue
        if bit + index_bits > total:
            break
        at = take(index_bits)
        if at == 0:
            break
        for k in range(take(length_bits) + 3):
            value = window[(at + k) & mask]
            out.append(value)
            window[position] = value
            position = (position + 1) & mask
    return bytes(out)


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _s32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">i", data, offset)[0]


def _s16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">h", data, offset)[0]


def _parse_wave(ctl: bytes, offset: int) -> WaveTable:
    base, length = _s32(ctl, offset), _s32(ctl, offset + 4)
    wave_type, flags = ctl[offset + 8], ctl[offset + 9]
    if wave_type != 0:
        raise AudioError(f"Unsupported wave table type {wave_type}; only ADPCM is used.")
    loop_offset, book_offset = _u32(ctl, offset + 12), _u32(ctl, offset + 16)
    order, predictors = _s32(ctl, book_offset), _s32(ctl, book_offset + 4)
    book = struct.unpack_from(f">{order * predictors * 8}h", ctl, book_offset + 8)
    loop = None
    if loop_offset:
        start, end, count = struct.unpack_from(">III", ctl, loop_offset)
        state = struct.unpack_from(">16h", ctl, loop_offset + 12)
        loop = AdpcmLoop(start, end, count, tuple(state))
    return WaveTable(base, length, wave_type, flags, order, predictors, tuple(book), loop)


def parse_bank(ctl: bytes, samples: bytes) -> SoundBank:
    revision, bank_count = struct.unpack_from(">HH", ctl, 0)
    if revision != 0x4231 or bank_count != 1:
        raise AudioError("Audio bank does not start with a single-bank 'B1' header.")
    bank = _u32(ctl, 4)
    instrument_count = _s16(ctl, bank)
    sample_rate = _s32(ctl, bank + 4)
    instruments = []
    for index in range(instrument_count):
        instrument_offset = _u32(ctl, bank + 12 + 4 * index)
        if instrument_offset == 0:
            instruments.append(Instrument(0, 0, 0, 0, ()))
            continue
        sounds = []
        for slot in range(_s16(ctl, instrument_offset + 14)):
            sound_offset = _u32(ctl, instrument_offset + 16 + 4 * slot)
            envelope_offset, key_offset, wave_offset = struct.unpack_from(">III", ctl, sound_offset)
            attack, decay, release = struct.unpack_from(">iii", ctl, envelope_offset)
            envelope = Envelope(attack, decay, release, ctl[envelope_offset + 12], ctl[envelope_offset + 13])
            sounds.append(Sound(envelope, KeyMap(*struct.unpack_from(">BBBBBb", ctl, key_offset)),
                                _parse_wave(ctl, wave_offset), ctl[sound_offset + 12], ctl[sound_offset + 13]))
        instruments.append(Instrument(ctl[instrument_offset], ctl[instrument_offset + 1],
                                      ctl[instrument_offset + 2], _s16(ctl, instrument_offset + 12),
                                      tuple(sounds)))
    return SoundBank(sample_rate, tuple(instruments), samples)


def load_audio(rom: bytes) -> AudioRom:
    """Read both banks and every song of a normalized DK64 US rev 0 ROM."""
    music = parse_bank(lzss_decompress(rom[slice(*MUSIC_CTL)]), rom[slice(*MUSIC_TBL)])
    sfx = parse_bank(lzss_decompress(rom[slice(*SFX_CTL)]), rom[slice(*SFX_TBL)])
    sounds = [sound for instrument in sfx.instruments for sound in instrument.sounds]
    effects = tuple(SoundEffect(index, index) for index in range(len(sounds)))
    songs = []
    for song_id in range(texture_bank.entry_count(rom, SONG_TABLE)):
        data = texture_bank.table_entry(rom, SONG_TABLE, song_id) or b""
        songs.append(Song(song_id, 127, 0, 0, bytes(data)))
    from .audio_reverb import load_fx_settings
    reverb, echo = load_fx_settings(rom)
    return AudioRom(music, sfx, effects, tuple(songs), reverb, echo)


def decode_adpcm(samples: bytes, wave: WaveTable) -> np.ndarray:
    """Decode one VADPCM wave table to signed 16-bit samples (JFG Forge decoder).

    Each 9-byte frame holds a header (scale in the high nibble, predictor in the low
    nibble) and 16 four-bit residuals; every block of eight samples is predicted from the
    previous two outputs and the earlier residuals with the wave's coefficient book.
    """
    frame_count = min(wave.length, max(len(samples) - wave.base, 0)) // 9
    if frame_count <= 0:
        return np.zeros(0, dtype=np.int16)
    data = np.frombuffer(samples, dtype=np.uint8, count=frame_count * 9, offset=wave.base)
    frames = data.reshape(frame_count, 9)
    order = wave.order
    book = np.asarray(wave.book, dtype=np.int64).reshape(wave.predictors, order, 8)
    previous, spread = [], []
    for predictor in range(wave.predictors):
        rows = book[predictor]
        previous.append((rows[0], rows[1]) if order >= 2 else (np.zeros(8, dtype=np.int64), rows[0]))
        matrix = np.zeros((8, 8), dtype=np.int64)
        for i in range(8):
            for j in range(i):
                matrix[i, j] = rows[order - 1][i - 1 - j]
        spread.append(matrix)
    headers = frames[:, 0].astype(np.int64)
    scales = headers >> 4
    predictors = np.where((headers & 0x0F) < wave.predictors, headers & 0x0F, 0)
    nibbles = np.empty((frame_count, 16), dtype=np.int64)
    nibbles[:, 0::2] = frames[:, 1:] >> 4
    nibbles[:, 1::2] = frames[:, 1:] & 0x0F
    nibbles = np.where(nibbles >= 8, nibbles - 16, nibbles) << scales[:, None]
    out = np.empty(frame_count * 16, dtype=np.int64)
    last1 = last2 = 0
    position = 0
    for frame in range(frame_count):
        row2, row1 = previous[predictors[frame]]
        matrix = spread[predictors[frame]]
        for half in range(2):
            residual = nibbles[frame, half * 8:half * 8 + 8]
            block = np.clip(((residual << 11) + row2 * last2 + row1 * last1 + matrix @ residual) >> 11,
                            -32768, 32767)
            out[position:position + 8] = block
            position += 8
            last2, last1 = int(block[6]), int(block[7])
    return out.astype(np.int16)
