"""Write rendered audio to WAV or MP3 files.

Adapted from MIT-licensed JFG Forge jfg_forge/core/audio_export.py (Copyright (c) 2026 Marvelmaster).
"""

from __future__ import annotations

from pathlib import Path
import wave

from .audio_render import Rendered

MP3_BITRATES = (128, 192, 256, 320)


class Mp3Unavailable(RuntimeError):
    """Raised when the optional MP3 encoder (``lameenc``) is not installed."""


def write_wav(path: Path | str, audio: Rendered) -> Path:
    """Write 16-bit PCM WAV (mono or stereo) at the audio's own sample rate."""
    path = Path(path)
    pcm = audio.to_int16()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(audio.channels)
        handle.setsampwidth(2)
        handle.setframerate(audio.sample_rate)
        handle.writeframes(pcm.tobytes())
    return path


def encode_mp3(audio: Rendered, bitrate: int = 192) -> bytes:
    try:
        import lameenc
    except ImportError as error:  # pragma: no cover - depends on the installation
        raise Mp3Unavailable("MP3 export needs the 'lameenc' package (pip install lameenc).") from error
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(audio.sample_rate)
    encoder.set_channels(audio.channels)
    encoder.set_quality(2)
    return bytes(encoder.encode(audio.to_int16().tobytes()) + encoder.flush())


def write_mp3(path: Path | str, audio: Rendered, bitrate: int = 192) -> Path:
    path = Path(path)
    path.write_bytes(encode_mp3(audio, bitrate))
    return path


def export_audio(path: Path | str, audio: Rendered) -> Path:
    """Write ``audio`` as WAV or MP3, chosen by the file name's extension."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return write_wav(path, audio)
    if suffix == ".mp3":
        return write_mp3(path, audio)
    raise ValueError(f"Unsupported audio export format {suffix!r}; use .wav or .mp3.")


__all__ = ["MP3_BITRATES", "Mp3Unavailable", "encode_mp3", "export_audio", "write_mp3", "write_wav"]
