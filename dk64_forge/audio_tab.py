"""The "Audio" tab: play and export DK64's music and sound effects.

Adapted from MIT-licensed JFG Forge jfg_forge/gui/audio_tab.py (Copyright (c) 2026 Marvelmaster).
"""

from __future__ import annotations

import re
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QElapsedTimer, QIODevice, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core.audio_export import Mp3Unavailable, export_audio
from .core.audio_names import sfx_label, sfx_note, song_name, song_note, song_type
from .core.audio_render import AudioEngine, Rendered
from .core.audio_rom import AudioRom, load_audio

try:  # QtMultimedia ships with PySide6 but may lack a working backend or device
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

    MULTIMEDIA_IMPORTED = True
except Exception:  # pragma: no cover - depends on the installation
    MULTIMEDIA_IMPORTED = False


def file_stem(kind: str, number: int, name: str, digits: int) -> str:
    """Default export file name: Song_04_Goldwood, Sound_487_Health_pickup."""
    clean = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return f"{kind}_{number:0{digits}d}" + (f"_{clean[:40]}" if clean else "")


def byte_offset(seconds: float, bytes_per_second: int, frame_bytes: int, size: int) -> int:
    """Byte position of ``seconds`` in a PCM buffer, on a whole-frame boundary and inside the buffer."""
    offset = int(max(seconds, 0.0) * bytes_per_second) // frame_bytes * frame_bytes
    return min(offset, max(size - frame_bytes, 0) // frame_bytes * frame_bytes)


class SeekSlider(QSlider):
    """A position bar you can click or drag to jump to a point in the clip.

    ``seek_requested`` carries the target as a fraction of the clip (0 to 1). While
    the mouse is down, seeks are sent at most every 60 ms so you hear the audio as you scrub.
    """

    seek_requested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.dragging = False
        self._clock = QElapsedTimer()

    def fraction(self) -> float:
        span = max(self.maximum() - self.minimum(), 1)
        return (self.value() - self.minimum()) / span

    def _value_at(self, event: QMouseEvent) -> int:
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(event.position().x()), max(self.width(), 1))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return super().mousePressEvent(event)
        self.dragging = True
        self.setValue(self._value_at(event))
        self._clock.start()
        self.seek_requested.emit(self.fraction())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.dragging:
            return super().mouseMoveEvent(event)
        self.setValue(self._value_at(event))
        if self._clock.elapsed() >= 60:
            self._clock.restart()
            self.seek_requested.emit(self.fraction())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self.dragging:
            return super().mouseReleaseEvent(event)
        self.dragging = False
        self.setValue(self._value_at(event))
        self.seek_requested.emit(self.fraction())
        event.accept()


def format_time(seconds: float) -> str:
    seconds = max(int(round(seconds)), 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


@dataclass(frozen=True)
class AudioEntry:
    index: int
    label: str
    tooltip: str = ""
    playable: bool = True
    looping: bool = False


def filter_entries(
    entries: list[AudioEntry],
    *,
    text: str = "",
    only_looping: bool = False,
    hide_unplayable: bool = True,
) -> list[AudioEntry]:
    """Apply the search text and checkboxes of an audio page (Qt-free, testable)."""
    needle = text.strip().lower()
    result = []
    for entry in entries:
        if hide_unplayable and not entry.playable:
            continue
        if only_looping and not entry.looping:
            continue
        if needle and needle not in entry.label.lower() and needle != str(entry.index):
            continue
        result.append(entry)
    return result


class Player(QObject):
    """Plays one rendered clip at a time through the default audio output."""

    started = Signal()
    finished = Signal()
    position = Signal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sink = None
        self._buffer: QBuffer | None = None
        self._bytes_per_second = 1
        self._frame_bytes = 2
        self._device = None
        self._format = None
        self._base_seconds = 0.0
        self._volume = 0.8
        self._playing = False
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    @property
    def available(self) -> bool:
        return MULTIMEDIA_IMPORTED and not QMediaDevices.defaultAudioOutput().isNull()

    @property
    def playing(self) -> bool:
        return self._playing

    def set_volume(self, volume: float) -> None:
        self._volume = min(max(volume, 0.0), 1.0)
        if self._sink is not None:
            self._sink.setVolume(self._volume)

    def play(self, audio: Rendered, start_seconds: float = 0.0) -> None:
        self.stop()
        if not self.available:
            raise RuntimeError("No audio output device is available.")
        device = QMediaDevices.defaultAudioOutput()
        samples = audio.samples
        rate = audio.sample_rate
        channels = audio.channels
        fmt = QAudioFormat()
        fmt.setSampleRate(rate)
        fmt.setChannelCount(channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            # Fall back to the device's preferred rate; resample by linear interpolation.
            preferred = device.preferredFormat()
            target = preferred.sampleRate() or 48000
            positions = np.arange(int(len(samples) * target / rate)) * (rate / target)
            index = np.arange(len(samples))
            if samples.ndim == 1:
                samples = np.interp(positions, index, samples).astype(np.float32)
            else:
                samples = np.column_stack(
                    [np.interp(positions, index, samples[:, c]) for c in range(channels)]
                ).astype(np.float32)
            rate = target
            fmt.setSampleRate(rate)
        pcm = np.clip(np.round(samples * 32767.0), -32768, 32767).astype("<i2")
        self._bytes_per_second = rate * channels * 2
        self._frame_bytes = channels * 2
        self._device, self._format = device, fmt
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(pcm.tobytes()))
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._open_sink(start_seconds)
        self._playing = True
        self._timer.start()
        self.started.emit()

    def _open_sink(self, seconds: float) -> None:
        """(Re)start the output at ``seconds`` in the loaded clip."""
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
        self._buffer.seek(byte_offset(seconds, self._bytes_per_second, self._frame_bytes, self._buffer.size()))
        self._base_seconds = self._buffer.pos() / self._bytes_per_second
        self._sink = QAudioSink(self._device, self._format, self)
        self._sink.setVolume(self._volume)
        self._sink.start(self._buffer)

    def seek(self, seconds: float) -> None:
        """Jump to ``seconds`` in the clip that is playing (no effect when nothing plays)."""
        if self._playing and self._buffer is not None:
            self._open_sink(seconds)
            self.position.emit(self._base_seconds)

    def stop(self) -> None:
        was_playing = self._playing
        self._timer.stop()
        self._playing = False
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
            self._buffer = None
        if was_playing:
            self.finished.emit()

    def _tick(self) -> None:
        if self._sink is None or self._buffer is None:
            return
        elapsed = self._base_seconds + self._sink.processedUSecs() / 1_000_000
        self.position.emit(elapsed)
        if self._buffer.atEnd() and elapsed * self._bytes_per_second >= self._buffer.size() - self._bytes_per_second * 0.05:
            self.stop()


_RENDER_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dk64-audio-render")


class AudioPage(QWidget):
    """One list of clips with playback and export controls."""

    def __init__(
        self,
        title: str,
        entries: list[AudioEntry],
        render: Callable[[int], Rendered | None],
        describe: Callable[[int, Rendered | None], list[tuple[str, str]]],
        default_name: Callable[[int], str],
        player: Player,
        *,
        background: bool,
        autoplay: bool,
        extra_filter: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self._entries = entries
        self._render = render
        self._describe = describe
        self._default_name = default_name
        self._player = player
        self._background = background
        self._cache: dict[int, Rendered] = {}
        self._current: int | None = None
        self._pending: int | None = None
        self._job: tuple[int, Future] | None = None
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._poll_job)
        self._duration = 0.0

        layout = QHBoxLayout(self)
        left = QFrame()
        left.setMinimumWidth(260)
        left_layout = QVBoxLayout(left)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search by number or name")
        self.search_edit.setClearButtonEnabled(True)
        left_layout.addWidget(self.search_edit)
        self.filter_check = QCheckBox(extra_filter) if extra_filter else None
        if self.filter_check is not None:
            left_layout.addWidget(self.filter_check)
        self.count_label = QLabel("")
        left_layout.addWidget(self.count_label)
        self.list_widget = QListWidget()
        left_layout.addWidget(self.list_widget, stretch=1)
        layout.addWidget(left, stretch=0)

        right = QFrame()
        right_layout = QVBoxLayout(right)
        self.heading = QLabel("Select an entry from the list.")
        self.heading.setStyleSheet("font-weight: bold; font-size: 16px;")
        right_layout.addWidget(self.heading)
        self.info_form = QFormLayout()
        self.info_values: dict[str, QLabel] = {}
        right_layout.addLayout(self.info_form)

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.stop_button = QPushButton("Stop")
        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        right_layout.addLayout(controls)
        self.progress = SeekSlider()
        self.progress.setRange(0, 1000)
        self.progress.setEnabled(False)
        right_layout.addWidget(self.progress)
        self.time_label = QLabel("0:00 / 0:00")
        right_layout.addWidget(self.time_label)
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Volume"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        volume_row.addWidget(self.volume_slider)
        right_layout.addLayout(volume_row)
        self.autoplay_check = QCheckBox("Play when selected")
        self.autoplay_check.setChecked(autoplay)
        right_layout.addWidget(self.autoplay_check)

        export_row = QHBoxLayout()
        self.wav_button = QPushButton("Export WAV...")
        self.mp3_button = QPushButton("Export MP3...")
        export_row.addWidget(self.wav_button)
        export_row.addWidget(self.mp3_button)
        right_layout.addLayout(export_row)
        self.export_all_button = QPushButton("Export all shown as WAV...")
        self.export_all_button.setToolTip("Render and export every entry of the current list "
                                          "(search and filters apply) as WAV files into one folder.")
        right_layout.addWidget(self.export_all_button)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        right_layout.addWidget(self.status_label)
        right_layout.addStretch(1)
        layout.addWidget(right, stretch=1)

        self.search_edit.textChanged.connect(self._refresh_list)
        if self.filter_check is not None:
            self.filter_check.toggled.connect(self._refresh_list)
        self.list_widget.currentItemChanged.connect(self._select_item)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.play_current())
        self.play_button.clicked.connect(self.play_current)
        self.stop_button.clicked.connect(self._player.stop)
        self.volume_slider.valueChanged.connect(lambda value: self._player.set_volume(value / 100.0))
        self.wav_button.clicked.connect(lambda: self.export_current("wav"))
        self.mp3_button.clicked.connect(lambda: self.export_current("mp3"))
        self.export_all_button.clicked.connect(lambda _checked=False: self.export_all())
        self.progress.seek_requested.connect(self._scrub)
        self._player.position.connect(self._position)
        self._player.finished.connect(self._playback_finished)
        self._refresh_list()

    # ------------------------------------------------------------------ list
    def _refresh_list(self, *_args: object) -> None:
        rows = filter_entries(
            self._entries,
            text=self.search_edit.text(),
            only_looping=bool(self.filter_check and self.filter_check.isChecked() and "loop" in self.filter_check.text().lower()),
            hide_unplayable=True,
        )
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        keep = self._current
        selected = -1
        for row, entry in enumerate(rows):
            item = QListWidgetItem(entry.label)
            item.setData(Qt.ItemDataRole.UserRole, entry.index)
            item.setToolTip(entry.tooltip)
            self.list_widget.addItem(item)
            if entry.index == keep:
                selected = row
        if selected >= 0:
            self.list_widget.setCurrentRow(selected)
        self.list_widget.blockSignals(False)
        self.count_label.setText(f"{len(rows)} of {len(self._entries)} entries")

    def _entry(self, index: int) -> AudioEntry:
        return next(entry for entry in self._entries if entry.index == index)

    def _select_item(self, item: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        if item is None:
            return
        self.select(int(item.data(Qt.ItemDataRole.UserRole)))

    def select(self, index: int) -> None:
        self._player.stop()
        self._current = index
        entry = self._entry(index)
        self.heading.setText(entry.label.strip())
        self._show_details(index, self._cache.get(index))
        self._reset_progress(self._cache[index].seconds if index in self._cache else 0.0)
        if self.autoplay_check.isChecked():
            self.play_current()

    def _show_details(self, index: int, rendered: Rendered | None) -> None:
        while self.info_form.rowCount():
            self.info_form.removeRow(0)
        self.info_values.clear()
        for name, value in self._describe(index, rendered):
            label = QLabel(value)
            label.setWordWrap(True)
            self.info_form.addRow(name, label)
            self.info_values[name] = label

    # ------------------------------------------------------------------ playback
    def rendered_for(self, index: int) -> Rendered | None:
        """Render (and cache) the clip synchronously."""
        if index not in self._cache:
            audio = self._render(index)
            if audio is None:
                return None
            self._cache[index] = audio
            while len(self._cache) > 6:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[index]

    def play_current(self) -> None:
        if self._current is None:
            return
        index = self._current
        if index in self._cache or not self._background:
            self._start(index)
            return
        self._pending = index
        self.status_label.setText("Rendering...")
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        self._job = (index, _RENDER_POOL.submit(self._render, index))
        self._poll.start()

    def _poll_job(self) -> None:
        if self._job is None:
            self._poll.stop()
            return
        index, future = self._job
        if not future.done():
            return
        self._poll.stop()
        self._job = None
        try:
            self._render_done(index, future.result())
        except Exception as error:
            self._render_failed(index, str(error))

    def _render_done(self, index: int, audio: object) -> None:
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        if audio is not None:
            self._cache[index] = audio  # type: ignore[assignment]
            while len(self._cache) > 6:
                self._cache.pop(next(iter(self._cache)))
        if self._pending == index and self._current == index:
            self._pending = None
            self._start(index)
        elif self._current == index:
            self._show_details(index, self._cache.get(index))

    def _render_failed(self, index: int, message: str) -> None:
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        self.status_label.setText(f"Could not render this entry: {message}")

    def _scrub(self, fraction: float) -> None:
        """Jump to a point of the current clip; start playing from there if it is stopped."""
        if self._current is None or self._duration <= 0:
            return
        seconds = min(max(fraction, 0.0), 1.0) * self._duration
        self.time_label.setText(f"{format_time(seconds)} / {format_time(self._duration)}")
        if self._player.playing:
            self._player.seek(seconds)
        elif self._current in self._cache:
            self._start(self._current, seconds)

    def _start(self, index: int, start_seconds: float = 0.0) -> None:
        try:
            audio = self.rendered_for(index)
        except Exception as error:
            self.status_label.setText(f"Could not render this entry: {error}")
            return
        if audio is None:
            self.status_label.setText("This entry has no sample to play.")
            return
        self._show_details(index, audio)
        self._reset_progress(audio.seconds)
        if not self._player.available:
            self.status_label.setText("No audio output device found; exporting still works.")
            return
        try:
            self._player.play(audio, start_seconds)
            if start_seconds > 0 and audio.seconds > 0:
                self.progress.setValue(min(int(start_seconds / audio.seconds * 1000), 1000))
        except Exception as error:
            self.status_label.setText(f"Playback failed: {error}")
            return
        self.status_label.setText("Playing.")
        self.play_button.setText("Restart")

    def _reset_progress(self, seconds: float) -> None:
        self._duration = seconds
        self.progress.setEnabled(seconds > 0)
        self.progress.setValue(0)
        self.time_label.setText(f"0:00 / {format_time(seconds)}")

    def _position(self, seconds: float) -> None:
        if self._duration > 0 and not self.progress.dragging:
            self.progress.setValue(min(int(seconds / self._duration * 1000), 1000))
        if not self.progress.dragging:
            self.time_label.setText(f"{format_time(seconds)} / {format_time(self._duration)}")

    def _playback_finished(self) -> None:
        self.play_button.setText("Play")
        if self.status_label.text() == "Playing.":
            self.status_label.setText("")
        if not self.progress.dragging:
            self.progress.setValue(0)

    # ------------------------------------------------------------------ export
    def shown_entries(self) -> list[AudioEntry]:
        return filter_entries(
            self._entries,
            text=self.search_edit.text(),
            only_looping=bool(self.filter_check and self.filter_check.isChecked()
                              and "loop" in self.filter_check.text().lower()),
            hide_unplayable=True,
        )

    def export_jobs(self, entries=None, kind: str = "wav"):
        """(label, job) pairs rendering and writing each entry (not kept in the play cache)."""
        if entries is None:
            entries = self.shown_entries()
        jobs = []
        for entry in entries:
            def job(folder: Path, index=entry.index):
                audio = self._cache.get(index) or self._render(index)
                if audio is None:
                    raise RuntimeError("this entry has no sample")
                export_audio(folder / f"{self._default_name(index)}.{kind}", audio)
            jobs.append((entry.label.split("   ")[0], job))
        return jobs

    def export_all(self, folder: Path | None = None, kind: str = "wav"):
        from .batch_export import export_all
        self._player.stop()
        result = export_all(self, f"Export {self.title.lower()}", self.export_jobs(kind=kind), folder)
        if result is not None:
            self.status_label.setText(result.summary() + ".")
        return result

    def export_current(self, kind: str) -> None:
        if self._current is None:
            QMessageBox.information(self, "DK64 Forge", "Select an entry to export first.")
            return
        index = self._current
        stem = self._default_name(index)
        filters = "WAV audio (*.wav)" if kind == "wav" else "MP3 audio (*.mp3)"
        destination, _selected = QFileDialog.getSaveFileName(
            self, f"Export {kind.upper()}", f"{stem}.{kind}", filters
        )
        if not destination:
            return
        path = Path(destination)
        if path.suffix.lower() != f".{kind}":
            path = path.with_suffix(f".{kind}")
        self.export_to(index, path)

    def export_to(self, index: int, path: Path) -> bool:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            audio = self.rendered_for(index)
            if audio is None:
                raise RuntimeError("this entry has no sample")
            export_audio(path, audio)
        except Mp3Unavailable as error:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "MP3 export unavailable", str(error))
            return False
        except Exception as error:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "DK64 Forge export error", str(error))
            self.status_label.setText(f"Export failed: {error}")
            return False
        QApplication.restoreOverrideCursor()
        self.status_label.setText(f"Exported {path.name} ({format_time(audio.seconds)}, {audio.sample_rate} Hz).")
        return True


class AudioTab(QWidget):
    """Music and Sounds segments over one shared player."""

    def __init__(self, source: bytes, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self.audio: AudioRom | None = None
        self.engine: AudioEngine | None = None
        self.player = Player(self)
        self.music_page: AudioPage | None = None
        self.sounds_page: AudioPage | None = None
        self.segments = QTabWidget()
        self.segments.setDocumentMode(True)
        self._placeholder = QLabel("Loading audio ...")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._placeholder)
        layout.addWidget(self.segments)
        self.segments.hide()
        self.segments.currentChanged.connect(lambda _index: self.player.stop())
        self.loaded = False

    def ensure_loaded(self) -> None:
        """Read the audio data the first time the tab is opened."""
        if self.loaded:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.audio = load_audio(self._source)
            self.engine = AudioEngine(self.audio)
            self._build_pages()
        except Exception as error:
            QApplication.restoreOverrideCursor()
            self._placeholder.setText(f"The audio data could not be read: {error}")
            return
        QApplication.restoreOverrideCursor()
        self.loaded = True
        self._placeholder.hide()
        self.segments.show()

    def _build_pages(self) -> None:
        audio, engine = self.audio, self.engine
        assert audio is not None and engine is not None

        song_entries = []
        for song in audio.songs:
            try:
                decoded = engine.sequence(song) if song.data else None
            except Exception:
                decoded = None
            notes = 0 if decoded is None else sum(1 for e in decoded.events if e.kind == "note")
            name = song_name(song.song_id)
            title = f"Song {song.song_id:03d}" + (f" - {name}" if name else "")
            length = format_time(decoded.seconds()) if decoded is not None else "-"
            song_entries.append(
                AudioEntry(
                    song.song_id,
                    f"{title}   {length}   {notes} notes",
                    "Empty or unreadable entry" if not notes else song_type(song.song_id),
                    playable=notes > 0,
                )
            )

        def describe_song(index: int, rendered: Rendered | None) -> list[tuple[str, str]]:
            song = audio.songs[index]
            decoded = engine.sequence(song)
            notes = [e for e in decoded.events if e.kind == "note"]
            tempo = next((e.a for e in decoded.events if e.kind == "tempo"), 500000)
            name = song_name(index)
            rows = [
                ("Name", name or "no known name"),
                ("Name source", song_note(index)),
                ("Type", song_type(index) or "-"),
                ("Song", f"{index} of {len(audio.songs) - 1} (pointer table 0)"),
                ("Notes", str(len(notes))),
                ("Tempo", f"{60_000_000 / tempo:.0f} beats per minute"),
                ("Instruments", str(len({e.a for e in decoded.events if e.kind == 'program'}))),
                ("Plays", "once through; an endless loop stops after one pass" if decoded.looped else "once through"),
            ]
            if rendered is not None:
                rows.append(("Rendered", f"{format_time(rendered.seconds)}, stereo, {rendered.sample_rate} Hz, game reverb (bus 0, stereo)"))
            return rows

        self.music_page = AudioPage(
            "Music",
            song_entries,
            engine.render_song,
            describe_song,
            lambda index: file_stem("Song", index, song_name(index), 3),
            self.player,
            background=True,
            autoplay=False,
        )

        effect_entries = []
        for effect in audio.effects:
            sound = engine.effect_sound(effect)
            loop = sound.wave.loop if sound is not None else None
            looping = bool(loop and loop.end > loop.start and loop.count != 0)
            effect_entries.append(
                AudioEntry(
                    effect.sound_id,
                    f"Sound {effect.sound_id:04d}"
                    + (f" - {sfx_label(effect.sound_id)}" if sfx_label(effect.sound_id) else "")
                    + ("   (loops)" if looping else ""),
                    "",
                    playable=sound is not None and sound.wave.length > 0,
                    looping=looping,
                )
            )

        def describe_effect(index: int, rendered: Rendered | None) -> list[tuple[str, str]]:
            effect = audio.effects[index]
            sound = engine.effect_sound(effect)
            rows = [
                ("Name", sfx_label(index) or "no known name"),
                ("Name source", sfx_note(index)),
                ("Sound", f"{index} of {len(audio.effects) - 1} (sound bank, instrument 0)"),
            ]
            if sound is not None:
                rows.append(("Sample", f"{sound.wave.length:,} bytes VADPCM, base key {sound.key_map.key_base}"))
                loop = sound.wave.loop
                if loop and loop.end > loop.start and loop.count != 0:
                    rows.append(("Looping", "yes; repeated for 3 seconds"))
            if rendered is not None:
                rows.append(("Length", f"{rendered.seconds:.2f} s, mono, {rendered.sample_rate} Hz"))
            return rows

        self.sounds_page = AudioPage(
            "Sounds",
            effect_entries,
            lambda index: engine.render_effect(index),
            describe_effect,
            lambda index: file_stem("Sound", index, sfx_label(index), 4),
            self.player,
            background=False,
            autoplay=True,
            extra_filter="Only looping sounds",
        )
        self.segments.addTab(self.music_page, "Music")
        self.segments.addTab(self.sounds_page, "Sounds")

    def stop(self) -> None:
        self.player.stop()


__all__ = ["AudioEntry", "AudioPage", "SeekSlider", "byte_offset", "file_stem", "AudioTab", "Player", "filter_entries", "format_time"]
