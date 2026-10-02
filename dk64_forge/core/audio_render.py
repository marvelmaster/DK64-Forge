"""Turn the ROM's audio data into PCM: single sound effects and whole songs.

Songs are rendered offline, note by note, from the decoded sequence and the
music bank.  This is an approximation of the console's synthesizer: it follows
the bank's key maps, pitch, envelopes, loops, channel volume and pan, and the
sequence's tempo, but has no chorus, sustain pedal or mid-note pitch
bend. DK64: songs go through the game's bus-0 reverb in its boot-default stereo mode
(dk64_forge.core.audio_reverb).

Adapted from MIT-licensed JFG Forge jfg_forge/core/audio_render.py (Copyright (c) 2026 Marvelmaster).
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .audio_rom import (
    DEFAULT_OUTPUT_RATE,
    AudioRom,
    Envelope,
    Sound,
    SoundBank,
    SoundEffect,
    Song,
    decode_adpcm,
)
from .audio_reverb import apply_reverb, send_levels
from .audio_sequence import DEFAULT_TEMPO_US, DecodedSequence, decode_sequence

LOOP_FOREVER = 0xFFFFFFFF
BANK_SIZE = 128  # programs per bank: bank-select LSB 1 reaches instruments 128 and up
BANK_SELECT_LSB = 32


def instrument_index(program: int, bank_lsb: int) -> int:
    """Music-bank instrument for a program change under the channel's bank select.

    Bank 0 is instruments 0-127; any non-zero bank-select LSB (controller 32) is the
    second bank, instruments 128 and up (31 of them, programs 0-30).
    """
    return program + (BANK_SIZE if bank_lsb else 0)
TAIL_SECONDS = 2.5  # room for the reverb to die away
EFFECT_SEND_CONTROLLER = 91
MAX_SONG_SECONDS = 600.0


@dataclass(frozen=True)
class Rendered:
    """Rendered audio: float32 samples in [-1, 1], shape (n,) for mono or (n, 2) for stereo."""

    samples: np.ndarray
    sample_rate: int

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.sample_rate

    @property
    def channels(self) -> int:
        return 1 if self.samples.ndim == 1 else self.samples.shape[1]

    def to_int16(self) -> np.ndarray:
        return np.clip(np.round(self.samples * 32767.0), -32768, 32767).astype(np.int16)


def cents_to_ratio(cents: float) -> float:
    return 2.0 ** (cents / 1200.0)


def _envelope(envelope: Envelope, count: int, rate: int, note_samples: int) -> np.ndarray:
    """Amplitude curve of length ``count``: attack, decay to the sustain level, release."""
    level = np.full(count, envelope.attack_volume / 127.0, dtype=np.float32)
    attack = int(max(envelope.attack_us, 0) * rate / 1_000_000)
    if attack > 0:
        attack = min(attack, count)
        level[:attack] = np.linspace(0.0, envelope.attack_volume / 127.0, attack, endpoint=False, dtype=np.float32)
    if envelope.decay_us > 0:
        decay = int(envelope.decay_us * rate / 1_000_000)
        end = min(attack + decay, count)
        if end > attack:
            level[attack:end] = np.linspace(
                envelope.attack_volume / 127.0, envelope.decay_volume / 127.0, end - attack, endpoint=False, dtype=np.float32
            )
        level[end:] = envelope.decay_volume / 127.0
    release = max(int(max(envelope.release_us, 0) * rate / 1_000_000), int(0.004 * rate))
    start = min(note_samples, count)
    if start < count:
        fade = min(release, count - start)
        start_level = level[start - 1] if start > 0 else level[0]
        level[start : start + fade] = np.linspace(start_level, 0.0, fade, endpoint=False, dtype=np.float32) if fade else level[start:start]
        level[start + fade :] = 0.0
    return level


def song_instruments(decoded: DecodedSequence) -> dict[int, int]:
    """Notes played per music-bank instrument index, with bank select applied."""
    program = [0] * 16
    pending = [0] * 16
    used: dict[int, int] = {}
    for event in decoded.events:
        if event.kind == "control" and event.a == BANK_SELECT_LSB:
            pending[event.channel] = event.b
        elif event.kind == "program":
            program[event.channel] = instrument_index(event.a, pending[event.channel])
        elif event.kind == "note":
            used[program[event.channel]] = used.get(program[event.channel], 0) + 1
    return used


class AudioEngine:
    """Decodes and caches wave tables, then renders effects and songs."""

    def __init__(self, audio: AudioRom, output_rate: int = DEFAULT_OUTPUT_RATE) -> None:
        self.audio = audio
        self.output_rate = output_rate
        self.stereo_reverb = True  # the game's boot default (sound mode 4)
        self._pcm: dict[tuple[int, int], np.ndarray] = {}
        self._sequences: dict[int, DecodedSequence] = {}
        self._sfx_sounds: list[Sound] = [s for i in audio.sfx_bank.instruments for s in i.sounds]

    # ------------------------------------------------------------------ samples
    def wave_pcm(self, bank: SoundBank, sound: Sound) -> np.ndarray:
        key = (id(bank), sound.wave.base)
        pcm = self._pcm.get(key)
        if pcm is None:
            pcm = decode_adpcm(bank.samples, sound.wave).astype(np.float32) / 32768.0
            self._pcm[key] = pcm
        return pcm

    def _voice(
        self,
        bank: SoundBank,
        sound: Sound,
        ratio: float,
        hold_seconds: float,
        rate: int,
    ) -> np.ndarray:
        """One note of ``sound`` at pitch ``ratio``, held ``hold_seconds`` before its release."""
        pcm = self.wave_pcm(bank, sound)
        step = ratio * bank.sample_rate / rate  # source samples per output sample
        loop = sound.wave.loop
        looping = loop is not None and loop.end > loop.start and loop.count != 0
        release = max(sound.envelope.release_us, 0) / 1_000_000 + 0.004
        if looping:
            hold_count = int(hold_seconds * rate)
            count = hold_count + int(release * rate)
            position = np.arange(count, dtype=np.float64) * step
            if loop.count != LOOP_FOREVER:
                # finite loops: play the body ``count`` extra times, then continue to the sample's end
                body = loop.end - loop.start
                limit = loop.end + loop.count * body
                over = position >= limit
                position = np.where(over, position - loop.count * body, position)
            span = loop.end - loop.start
            past = position >= loop.end
            position = np.where(past, loop.start + (position - loop.start) % span, position)
            values = np.interp(position, np.arange(len(pcm)), pcm)
            note_samples = hold_count
        else:
            natural = int(len(pcm) / step)
            hold_count = min(int(hold_seconds * rate), natural)
            count = natural if hold_seconds * rate >= natural else hold_count + int(release * rate)
            count = max(min(count, natural + int(release * rate)), 1)
            position = np.arange(count, dtype=np.float64) * step
            values = np.interp(position, np.arange(len(pcm)), pcm, right=0.0)
            note_samples = min(hold_count, count)
            if hold_seconds * rate >= natural:
                note_samples = count  # ran to the end of the sample; only the short fade remains
        envelope = _envelope(sound.envelope, len(values), rate, note_samples)
        return (values * envelope).astype(np.float32)

    # ------------------------------------------------------------------ effects
    def effect_count(self) -> int:
        return len(self.audio.effects)

    def effect_sound(self, effect: SoundEffect) -> Sound | None:
        if 0 <= effect.bite < len(self._sfx_sounds):
            return self._sfx_sounds[effect.bite]
        return None

    def render_effect(self, sound_id: int, max_loop_seconds: float = 3.0) -> Rendered | None:
        """Render one game sound the way its index entry plays it (pitch and volume applied).

        Looped sounds are repeated for ``max_loop_seconds``.  Returns ``None`` for the few
        index entries that point outside the sample bank.
        """
        effect = self.audio.effects[sound_id]
        sound = self.effect_sound(effect)
        if sound is None:
            return None
        bank = self.audio.sfx_bank
        rate = bank.sample_rate
        ratio = (effect.pitch / 100.0) * cents_to_ratio(sound.key_map.detune_cents)
        voice = self._voice(bank, sound, ratio, max_loop_seconds, rate)
        gain = min(effect.volume / 128.0, 1.0) * (sound.volume / 127.0 if sound.volume else 1.0)
        return Rendered(np.clip(voice * gain, -1.0, 1.0).astype(np.float32), rate)

    # ------------------------------------------------------------------ songs
    def sequence(self, song: Song) -> DecodedSequence:
        decoded = self._sequences.get(song.song_id)
        if decoded is None:
            decoded = decode_sequence(song.data)
            self._sequences[song.song_id] = decoded
        return decoded

    def song_instruments(self, song: Song) -> dict[int, int]:
        """Notes played per music-bank instrument index, with bank select applied."""
        return song_instruments(self.sequence(song))

    def song_is_silent(self, song: Song) -> bool:
        return not any(e.kind == "note" for e in self.sequence(song).events)

    def _pick_sound(self, instrument, note: int, velocity: int) -> Sound | None:
        best: Sound | None = None
        best_distance = 1 << 30
        for sound in instrument.sounds:
            km = sound.key_map
            if km.key_min <= note <= km.key_max and km.velocity_min <= velocity <= km.velocity_max:
                return sound
            distance = min(abs(note - km.key_min), abs(note - km.key_max))
            if distance < best_distance:
                best, best_distance = sound, distance
        return best

    def render_song(
        self,
        song_id: int,
        progress: Callable[[float], None] | None = None,
    ) -> Rendered:
        """Render one song, once through, to stereo audio."""
        song = self.audio.songs[song_id]
        decoded = self.sequence(song)
        rate = self.output_rate
        bank = self.audio.music_bank

        tempo_ticks = [0]
        tempo_us = [DEFAULT_TEMPO_US]
        tempo_seconds = [0.0]
        for event in decoded.events:
            if event.kind == "tempo":
                index = len(tempo_ticks) - 1
                seconds = tempo_seconds[index] + (event.tick - tempo_ticks[index]) * tempo_us[index] / (decoded.division * 1e6)
                tempo_ticks.append(event.tick)
                tempo_us.append(event.a)
                tempo_seconds.append(seconds)

        def to_seconds(tick: int) -> float:
            index = bisect.bisect_right(tempo_ticks, tick) - 1
            return tempo_seconds[index] + (tick - tempo_ticks[index]) * tempo_us[index] / (decoded.division * 1e6)

        notes = [e for e in decoded.events if e.kind == "note"]
        if not notes:
            return Rendered(np.zeros((int(0.5 * rate), 2), dtype=np.float32), rate)
        end_seconds = max(to_seconds(e.tick + e.duration) for e in notes)
        total = min(end_seconds + TAIL_SECONDS, MAX_SONG_SECONDS)
        mix = np.zeros((int(total * rate) + rate, 2), dtype=np.float32)
        reverb = self.audio.reverb
        aux = np.zeros_like(mix) if reverb is not None else None  # what each channel sends to the effect

        program = [0] * 16  # instrument index per channel (bank select already applied)
        pending_bank = [0] * 16  # bank select takes effect at the next program change
        fx_send = [0] * 16  # controller 91: share of the channel that goes to the reverb
        volume = [100] * 16
        pan = [64] * 16
        bend = [8192] * 16
        song_gain = (song.volume if song.volume else 127) / 127.0
        done = 0
        for event in decoded.events:
            channel = event.channel
            if event.kind == "program":
                program[channel] = instrument_index(event.a, pending_bank[channel])
            elif event.kind == "control":
                if event.a == BANK_SELECT_LSB:
                    pending_bank[channel] = event.b
                elif event.a == EFFECT_SEND_CONTROLLER:
                    fx_send[channel] = event.b
                elif event.a == 7:
                    volume[channel] = event.b
                elif event.a == 10:
                    pan[channel] = event.b
            elif event.kind == "bend":
                bend[channel] = event.b
            elif event.kind == "note":
                start = to_seconds(event.tick)
                if start >= total:
                    continue
                if program[channel] >= len(bank.instruments):
                    continue
                instrument = bank.instruments[program[channel]]
                sound = self._pick_sound(instrument, event.a, event.b)
                if sound is None:
                    continue
                km = sound.key_map
                cents = (event.a - km.key_base) * 100 + km.detune_cents
                if instrument.bend_range:
                    cents += (bend[channel] - 8192) / 8192.0 * instrument.bend_range
                hold = to_seconds(event.tick + event.duration) - start
                voice = self._voice(bank, sound, cents_to_ratio(cents), max(hold, 0.01), rate)
                inst_gain = (instrument.volume if instrument.volume else 127) / 127.0
                gain = (
                    (event.b / 127.0)
                    * (volume[channel] / 127.0)
                    * inst_gain
                    * (sound.volume / 127.0 if sound.volume else 1.0)
                    * song_gain
                    * 0.5
                )
                angle = min(max(pan[channel] + sound.pan - 64, 0), 127) / 127.0 * (math.pi / 2)  # the sample's own pan offsets the channel's
                offset = int(start * rate)
                room = len(mix) - offset
                if room <= 0:
                    continue
                voice = voice[:room]
                dry, wet = send_levels(fx_send[channel]) if reverb is not None else (1.0, 0.0)
                left, right = voice * gain * math.cos(angle), voice * gain * math.sin(angle)
                mix[offset : offset + len(voice), 0] += left * dry
                mix[offset : offset + len(voice), 1] += right * dry
                if aux is not None and wet > 0.0:
                    aux[offset : offset + len(voice), 0] += left * wet
                    aux[offset : offset + len(voice), 1] += right * wet
                done += 1
                if progress and done % 200 == 0:
                    progress(min(start / total, 1.0))
        if reverb is not None and aux is not None and np.any(aux):
            wet_left, wet_right = apply_reverb(aux[:, 0], aux[:, 1], reverb, rate, stereo=self.stereo_reverb)
            mix[:, 0] += wet_left
            mix[:, 1] += wet_right
        # Songs differ a lot in loudness; scale each one to a comfortable, non-clipping peak.
        peak = float(np.max(np.abs(mix)))
        if peak > 0:
            mix *= min(0.9 / peak, 6.0)
        # trim trailing silence but keep the tail
        keep = int(total * rate)
        mix = mix[:keep]
        if progress:
            progress(1.0)
        return Rendered(mix, rate)


__all__ = ["AudioEngine", "Rendered", "cents_to_ratio", "instrument_index", "song_instruments"]
