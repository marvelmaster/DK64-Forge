"""DK64's song reverb, rebuilt from the game's own effect settings and mixing code.

Evidence (decompilation, CC0, plus the ROM's own code and data):

* ``func_global_asm_80600D50`` (code_5A50.c) configures the synthesizer with two
  auxiliary effect buses of type 6 (``AL_FX_CUSTOM``); ``func_global_asm_80601A10``
  (not yet matched; read from the ROM's MIPS code) copies a 0x218-byte table from RAM
  ``0x807452D0`` (global_asm .data) and points ``params[0]`` at its start and
  ``params[1]`` at +0x10C. ``n_alFxNew`` (n_drvrNew.c) reads them in the libultra
  layout: section count, delay length, then per section input, output, fbcoef, ffcoef,
  gain, chorus rate, chorus depth, low-pass coefficient.
  Bus 0 is a room reverb (8 sections over 8,816 samples, the last a silent chorus);
  bus 1 is a single echo (2,200 samples). Songs never select a bus (controller 92), so
  they use bus 0.
* Each voice sends ``eqpower[127 - fxmix]`` of its panned output to the bus and keeps
  ``eqpower[fxmix]`` dry (n_env.c); controller 91 sets ``fxmix``.
* At boot the game sets sound mode 4 and bus-0 mode 4 (``func_global_asm_80737C20`` and
  ``func_global_asm_80737CF4``), which selects the **stereo** effect: one delay line per
  side. ``n_alFxPull`` (n_reverb.c) mixes the left result into the left output at
  0x5A82 (0.7071), and ``func_global_asm_8073FD90`` (code_144A90.c) mixes the right
  result into the right output at 0x7FFF. The mono variant (sound mode 1) feeds
  0.5 * (left + right) into one line, scales each section gain by 1.4142 and sends the
  result to both sides.

The section algorithm (load the input and output taps, feed-forward, feedback, optional
one-pole low pass, write back, add ``gain`` times the output tap) is adapted from
MIT-licensed jfg_forge.core.audio_reverb (Copyright (c) 2026 Marvelmaster); Jet Force
Gemini runs the same libultra effect. Delays are in samples of the console's 22,050 Hz
output and are scaled to Forge's output rate.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
import zlib

import numpy as np

CONSOLE_RATE = 22050   # osAiSetFrequency(22050) and the synthesizer's outputRate
CONSOLE_BLOCK = 184    # FIXED_SAMPLE: n_reverb processes the delay line in 184-sample steps
SCALE = 32768.0

GLOBAL_ASM_ROM = (0x113F0, 0xCBE70)   # gOverlayTable[1]: gzip code, then gzip .data
GLOBAL_ASM_VRAM = 0x805FB300
FX_PARAMS_VRAM = 0x807452D0
FX_PARAMS_SIZE = 0x218
BUS1_OFFSET = 0x10C


@dataclass(frozen=True)
class ReverbSection:
    input: int
    output: int
    feedback: int
    feedforward: int
    gain: int
    chorus_rate: int
    chorus_depth: int
    low_pass: int


@dataclass(frozen=True)
class ReverbSettings:
    length: int
    sections: tuple[ReverbSection, ...]


def parse_params(blob: bytes) -> ReverbSettings:
    count, length = struct.unpack_from(">ii", blob, 0)
    if not 0 < count <= 16 or length <= 0 or len(blob) < 8 + 32 * count:
        raise ValueError("implausible effect parameter block")
    sections = tuple(ReverbSection(*struct.unpack_from(">8i", blob, 8 + 32 * i)) for i in range(count))
    return ReverbSettings(length, sections)


def load_fx_settings(rom: bytes) -> tuple[ReverbSettings, ReverbSettings]:
    """Both effect buses' settings from the ROM's global_asm .data."""
    stream = zlib.decompressobj(16 + 15)
    code = stream.decompress(rom[slice(*GLOBAL_ASM_ROM)])
    data = zlib.decompressobj(16 + 15).decompress(stream.unused_data)
    blob = (code + data)[FX_PARAMS_VRAM - GLOBAL_ASM_VRAM:][:FX_PARAMS_SIZE]
    return parse_params(blob), parse_params(blob[BUS1_OFFSET:])


def eqpower(index: int) -> float:
    """The library's equal-power table: cos(index / 127 * pi / 2), ending in an exact zero."""
    return 0.0 if index >= 127 else float(np.cos(index / 127.0 * np.pi / 2.0))


def send_levels(fxmix: int) -> tuple[float, float]:
    """(dry, wet) amounts for a channel's effect send (controller 91) of 0 to 127."""
    fxmix = min(max(int(fxmix), 0), 127)
    return eqpower(fxmix), eqpower(127 - fxmix)


def _run_line(signal: np.ndarray, settings: ReverbSettings, rate: int, gain_scale: float) -> np.ndarray:
    """One delay line of the effect over ``signal``; returns the summed section outputs."""
    ratio = rate / CONSOLE_RATE
    block = max(int(round(CONSOLE_BLOCK * ratio)), 16)
    sections = settings.sections
    delays = [(int(round(s.input * ratio)), int(round(s.output * ratio))) for s in sections]
    pad = max(max(a, b) for a, b in delays) + block + 16
    total = len(signal)
    line = np.zeros(pad + total + block, dtype=np.float32)
    result = np.zeros(total + block, dtype=np.float32)
    source = np.concatenate([signal.astype(np.float32), np.zeros(block, dtype=np.float32)])
    taps = 96
    lp_kernel, lp_history = [], []
    for s in sections:
        a = (min(max(s.low_pass, 0), 32767) / SCALE) ** (1.0 / ratio) if s.low_pass else 0.0
        lp_kernel.append(((1.0 - a) * a ** np.arange(taps)).astype(np.float64))
        lp_history.append(np.zeros(taps - 1, dtype=np.float64))
    gains = [min(s.gain * gain_scale, 32767.0) / SCALE for s in sections]
    for start in range(0, total, block):
        here = pad + start
        line[here:here + block] = source[start:start + block]
        out = np.zeros(block, dtype=np.float32)
        for index, section in enumerate(sections):
            d_in, d_out = delays[index]
            in_at, out_at = here - d_in, here - d_out
            buff1 = line[in_at:in_at + block].copy()
            buff2 = line[out_at:out_at + block].copy()
            chorus = section.chorus_rate != 0
            low_pass = section.low_pass != 0
            if section.feedforward:
                buff2 += buff1 * (section.feedforward / SCALE)
                if not chorus and not low_pass:
                    line[out_at:out_at + block] = buff2
            if section.feedback:
                buff1 += buff2 * (section.feedback / SCALE)
                line[in_at:in_at + block] = buff1
            if low_pass:
                joined = np.concatenate([lp_history[index], buff2.astype(np.float64)])
                lp_history[index] = joined[-(taps - 1):]
                buff2 = np.convolve(joined, lp_kernel[index], mode="valid")[:block].astype(np.float32)
            if not chorus:
                line[out_at:out_at + block] = buff2
            if gains[index]:
                out += buff2 * gains[index]
        result[start:start + block] = out
    return result[:total]


def apply_reverb(send_left: np.ndarray, send_right: np.ndarray, settings: ReverbSettings,
                 rate: int, stereo: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """The effect's (left, right) output for the summed effect sends, as the game mixes it."""
    if stereo:
        left = _run_line(send_left, settings, rate, 1.0) * 0.7071
        right = _run_line(send_right, settings, rate, 1.0)
        return left, right
    mono = _run_line(0.5 * (send_left + send_right), settings, rate, 1.4142)
    return mono, mono
