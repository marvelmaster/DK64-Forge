"""Decoder for DK64's compressed MIDI ("CSeq") sequences (pointer table 0).

Format (libultra ``cseq``): a header of sixteen track offsets and a division
(ticks per quarter note), followed by the track streams.  Each event is a
variable-length delta time and a MIDI status/data group; note-on events carry an
extra variable-length duration instead of a separate note-off.  Repeated runs of
bytes are stored as back-references (byte ``0xFE``), and loops are stored as
meta events with an in-stream repeat counter.

Adapted from MIT-licensed JFG Forge jfg_forge/core/audio_sequence.py (Copyright (c) 2026 Marvelmaster).
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

from .audio_rom import AudioError as BoyExportError

BLOCK_CODE = 0xFE
META = 0xFF
META_TEMPO = 0x51
META_END_OF_TRACK = 0x2F
META_LOOP_START = 0x2E
META_LOOP_END = 0x2D
DEFAULT_TEMPO_US = 500_000

NOTE_OFF = 0x80
NOTE_ON = 0x90
POLY_PRESSURE = 0xA0
CONTROL_CHANGE = 0xB0
PROGRAM_CHANGE = 0xC0
CHANNEL_PRESSURE = 0xD0
PITCH_BEND = 0xE0


@dataclass(frozen=True)
class SequenceEvent:
    tick: int
    channel: int
    kind: str  # note, program, control, bend, tempo, end
    a: int = 0  # note / program / controller number / tempo (microseconds per quarter)
    b: int = 0  # velocity / controller value / bend (14 bit)
    duration: int = 0  # ticks (notes only)


@dataclass(frozen=True)
class DecodedSequence:
    division: int
    events: tuple[SequenceEvent, ...]
    total_ticks: int
    looped: bool  # True if an endless loop ended the song early

    def seconds(self) -> float:
        """Length of the decoded part in seconds, following the tempo changes."""
        tempo = DEFAULT_TEMPO_US
        last_tick = 0
        seconds = 0.0
        for event in self.events:
            seconds += (event.tick - last_tick) * tempo / (self.division * 1_000_000)
            last_tick = event.tick
            if event.kind == "tempo":
                tempo = event.a
        seconds += (self.total_ticks - last_tick) * tempo / (self.division * 1_000_000)
        return seconds


class _Track:
    __slots__ = ("loc", "backup_ptr", "backup_len", "last_status", "delta")

    def __init__(self, loc: int) -> None:
        self.loc = loc
        self.backup_ptr = 0
        self.backup_len = 0
        self.last_status = 0
        self.delta = 0


class _Reader:
    def __init__(self, data: bytearray) -> None:
        self.data = data

    def byte(self, track: _Track) -> int:
        data = self.data
        if track.backup_len:
            value = data[track.backup_ptr]
            track.backup_ptr += 1
            track.backup_len -= 1
            return value
        value = data[track.loc]
        track.loc += 1
        if value == BLOCK_CODE:
            following = data[track.loc]
            track.loc += 1
            if following != BLOCK_CODE:
                high = following
                low = data[track.loc]
                track.loc += 1
                length = data[track.loc]
                track.loc += 1
                back = (high << 8) + low
                track.backup_ptr = track.loc - (back + 4)
                track.backup_len = length
                value = data[track.backup_ptr]
                track.backup_ptr += 1
                track.backup_len -= 1
        return value

    def varlen(self, track: _Track) -> int:
        value = self.byte(track)
        if value & 0x80:
            value &= 0x7F
            while True:
                c = self.byte(track)
                value = (value << 7) + (c & 0x7F)
                if not c & 0x80:
                    break
        return value


def decode_sequence(data: bytes, max_events: int = 400_000) -> DecodedSequence:
    """Decode one compressed sequence into a time-ordered event list.

    A track ends at its end-of-track event or at the first endless loop, so the
    result plays the song once through (intro and one pass of the loop body).
    """
    if len(data) < 68:
        raise BoyExportError("Sequence is shorter than its 68-byte header.")
    offsets = struct.unpack_from(">16I", data, 0)
    division = struct.unpack_from(">I", data, 64)[0]
    if division == 0:
        raise BoyExportError("Sequence division is zero.")
    buffer = bytearray(data)  # loop counters are stored in the stream and change while playing
    reader = _Reader(buffer)
    tracks: dict[int, _Track] = {}
    for index, offset in enumerate(offsets):
        if offset and offset < len(buffer):
            track = _Track(offset)
            track.delta = reader.varlen(track)
            tracks[index] = track

    events: list[SequenceEvent] = []
    now = 0
    looped = False
    try:
        while tracks and len(events) < max_events:
            index = min(tracks, key=lambda i: (tracks[i].delta, i))
            track = tracks[index]
            step = track.delta
            now += step
            for other in tracks.values():
                other.delta -= step
            status = reader.byte(track)
            finished = False
            if status == META:
                kind = reader.byte(track)
                if kind == META_TEMPO:
                    tempo = (reader.byte(track) << 16) | (reader.byte(track) << 8) | reader.byte(track)
                    events.append(SequenceEvent(now, index, "tempo", tempo))
                    track.last_status = 0
                elif kind == META_END_OF_TRACK:
                    finished = True
                elif kind == META_LOOP_START:
                    reader.byte(track)
                    reader.byte(track)
                    track.last_status = 0
                elif kind == META_LOOP_END:
                    location = track.loc
                    loop_count = buffer[location]
                    current = buffer[location + 1]
                    if current == 0:
                        buffer[location + 1] = loop_count
                        track.loc = location + 1 + 5 - 1 + 0  # skip count, current, four offset bytes
                        track.loc = location + 6
                    elif current == 0xFF:
                        looped = True
                        finished = True
                    else:
                        buffer[location + 1] = current - 1
                        back = struct.unpack_from(">I", buffer, location + 2)[0]
                        track.loc = location + 6 - back
                    track.last_status = 0
                else:
                    raise BoyExportError(f"Unknown sequence meta event {kind:#x}.")
            else:
                if status & 0x80:
                    first = reader.byte(track)
                    track.last_status = status
                else:
                    first = status
                    status = track.last_status
                    if status == 0:
                        raise BoyExportError("Running status used before any status byte.")
                high, channel = status & 0xF0, status & 0x0F
                if high in (PROGRAM_CHANGE, CHANNEL_PRESSURE):
                    if high == PROGRAM_CHANGE:
                        events.append(SequenceEvent(now, channel, "program", first))
                else:
                    second = reader.byte(track)
                    if high == NOTE_ON:
                        duration = reader.varlen(track)
                        if second:
                            events.append(SequenceEvent(now, channel, "note", first, second, duration))
                    elif high == CONTROL_CHANGE:
                        events.append(SequenceEvent(now, channel, "control", first, second))
                    elif high == PITCH_BEND:
                        events.append(SequenceEvent(now, channel, "bend", 0, (second << 7) | first))
            if finished:
                del tracks[index]
            else:
                track.delta += reader.varlen(track)
    except IndexError as error:
        raise BoyExportError("Sequence data ended unexpectedly.") from error
    end_tick = max(
        [now] + [event.tick + event.duration for event in events if event.kind == "note"]
    )
    return DecodedSequence(division, tuple(events), end_tick, looped)


__all__ = ["DecodedSequence", "SequenceEvent", "decode_sequence"]
