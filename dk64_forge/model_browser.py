"""Models and Levels tabs: browse every actor model, prop and map as a static, textured mesh.

Layout follows MIT-licensed jfg_forge.gui.prop_browser / level_browser (Copyright 2026
Marvelmaster): searchable list, viewport, info panel, static export. DK64 decoding is
dk64_forge.core.mesh_decoder via dk64_forge.static_model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSlider, QSplitter, QVBoxLayout, QWidget,
)

from . import static_model
from .core import names, texture_bank
from .debug_view import ViewMode

KIND_ACTOR, KIND_PROP, KIND_MAP = "actor", "prop", "map"
KIND_LABELS = {KIND_ACTOR: "Actor", KIND_PROP: "Prop", KIND_MAP: "Map"}
KIND_TABLES = {KIND_ACTOR: 5, KIND_PROP: 4, KIND_MAP: 1}
SHOW_ALL = "All"
LOADERS = {KIND_ACTOR: static_model.actor_model, KIND_PROP: static_model.prop_model,
           KIND_MAP: static_model.map_model}


@dataclass(frozen=True)
class BrowserEntry:
    kind: str
    index: int
    name: str
    name_source: str

    @property
    def label(self) -> str:
        return f"{KIND_LABELS[self.kind]} {self.index:03X}  {self.name}"


def catalog(rom: bytes, kinds) -> list[BrowserEntry]:
    """Every non-empty entry of the given kinds with its best available name."""
    entries = []
    for kind in kinds:
        table = KIND_TABLES[kind]
        for index in range(texture_bank.entry_count(rom, table)):
            data = texture_bank.table_entry(rom, table, index)
            if not data:
                continue
            if kind == KIND_MAP and (len(data) < 0x140 or data[2:4] == b"\x08\x00"):
                continue  # pointer stubs share another map's geometry
            name, source = None, ""
            if kind == KIND_ACTOR and index < len(names.ACTOR_MODEL_NAMES):
                name, source = names.ACTOR_MODEL_NAMES[index], "DK64 Randomizer model list (COMMUNITY)"
            elif kind == KIND_PROP:
                name, source = texture_bank.prop_name(rom, index), "ROM header 0x0C"
            elif kind == KIND_MAP:
                name, source = names.MAP_NAMES.get(index), "decompilation map enum (COMMUNITY)"
            entries.append(BrowserEntry(kind, index, name or "(unnamed)", source if name else "none"))
    return entries


def filter_entries(entries, text: str = "", kind: str = SHOW_ALL):
    needle = text.strip().lower()
    rows = []
    for entry in entries:
        if kind != SHOW_ALL and entry.kind != kind:
            continue
        if needle and not (needle in entry.name.lower() or needle == str(entry.index)
                           or needle in (f"{entry.index:x}", f"0x{entry.index:x}")):
            continue
        rows.append(entry)
    return rows


def safe_file_stem(entry: BrowserEntry) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", entry.name).strip("_")
    return f"{KIND_LABELS[entry.kind]}_{entry.index:03X}_{name}"[:80]


class ModelBrowserTab(QWidget):
    status_message = Signal(str)

    def __init__(self, rom: bytes, kinds, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rom = rom
        self._kinds = tuple(kinds)
        self._title = title
        self._entries: list[BrowserEntry] = []
        self._cache = static_model.TextureCache(rom)
        self._current: tuple[BrowserEntry, static_model.StaticModel] | None = None
        self.viewport = None
        self._content = None
        self._content_models = {}
        self._actor_playback = None
        self._captured = []
        self._actor_animations = None
        self._animation_assets = None
        self._scene_tick = 0
        self._map_playback = None
        self._content_playback = None
        self._scene_timer = QTimer(self)
        self._scene_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._scene_timer.setInterval(16)
        self._scene_timer.timeout.connect(self._playback_frame)
        self._playback_time = None
        self._playback_remainder = 0.0
        self.loaded = False
        from .background import Loader
        from collections import OrderedDict
        self._assets = OrderedDict()
        self._loader = Loader(self)
        self._loader.progress.connect(self.status_message)
        self._loader.failed.connect(self.status_message)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setWidget(self._build_side_panel())
        splitter.addWidget(side_scroll)
        self._view_area = QFrame()
        self._view_layout = QVBoxLayout(self._view_area)
        self._view_layout.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QLabel(f"Select an entry from the list.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view_layout.addWidget(self._placeholder)
        splitter.addWidget(self._view_area)
        splitter.setSizes([360, 820])
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def _build_side_panel(self) -> QWidget:
        panel = QFrame()
        panel.setMinimumWidth(280)
        layout = QVBoxLayout(panel)
        heading = QLabel(self._title)
        heading.setStyleSheet("font-weight: bold; font-size: 15px;")
        layout.addWidget(heading)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search name or number (decimal or 0x hex)")
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem(SHOW_ALL, SHOW_ALL)
        for kind in self._kinds:
            self.kind_combo.addItem(KIND_LABELS[kind] + "s", kind)
        self.kind_combo.setVisible(len(self._kinds) > 1)
        layout.addWidget(self.kind_combo)
        self.count_label = QLabel("Open the tab to list the entries.")
        layout.addWidget(self.count_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        self._loader.busy.connect(self.progress_bar.setVisible)
        layout.addWidget(self.progress_bar)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, stretch=1)
        info = QFormLayout()
        self.name_value = QLabel("-")
        self.name_value.setWordWrap(True)
        self.source_value = QLabel("-")
        self.source_value.setWordWrap(True)
        self.geometry_value = QLabel("-")
        self.texture_value = QLabel("-")
        self.texture_value.setWordWrap(True)
        self.notes_value = QLabel("-")
        self.notes_value.setWordWrap(True)
        for label, widget in (("Name", self.name_value), ("Name source", self.source_value),
                              ("Geometry", self.geometry_value), ("Textures", self.texture_value),
                              ("Notes", self.notes_value)):
            info.addRow(label, widget)
        layout.addLayout(info)
        self.load_clips_button = QPushButton("Find compatible actor clips")
        self.load_clips_button.clicked.connect(self._load_actor_clips)
        layout.addWidget(self.load_clips_button)
        self.clip_combo = QComboBox()
        self.clip_combo.addItem("Static rest pose", None)
        self.clip_combo.currentIndexChanged.connect(self._select_actor_clip)
        layout.addWidget(self.clip_combo)
        self.clip_frame = QSlider(Qt.Orientation.Horizontal)
        self.clip_frame.setRange(0, 0)
        self.clip_frame.valueChanged.connect(self._show_actor_frame)
        layout.addWidget(self.clip_frame)
        self.clip_export = QPushButton("Export actor + clip GLB...")
        self.clip_export.setEnabled(False)
        self.clip_export.clicked.connect(self._export_actor_clip)
        layout.addWidget(self.clip_export)
        for widget in (self.load_clips_button, self.clip_combo, self.clip_frame, self.clip_export):
            widget.setVisible(KIND_ACTOR in self._kinds or KIND_PROP in self._kinds)
        self.prop_speed = QSpinBox()
        self.prop_speed.setRange(1, 300)
        self.prop_speed.setValue(1)
        self.prop_speed.setPrefix("Prop script speed ")
        self.prop_speed.setToolTip("Runtime track multiplier; object scripts choose it in the game. Preview loops the selected track forward.")
        self.prop_speed.setVisible(KIND_PROP in self._kinds)
        self.prop_speed.valueChanged.connect(lambda: self._show_actor_frame(self.clip_frame.value()))
        layout.addWidget(self.prop_speed)
        self.scene_play = QPushButton("Play")
        self.scene_play.clicked.connect(self._toggle_scene_play)
        layout.addWidget(self.scene_play)
        self.content_check = QCheckBox("Show placed props and actor spawns")
        self.content_check.setVisible(KIND_MAP in self._kinds)
        self.content_check.toggled.connect(self._load_content_async)
        layout.addWidget(self.content_check)
        self.night_check = QCheckBox("Fungi Forest night spawns")
        self.night_check.setToolTip("Source-defined enemy replacements on map 48; other game conditions are unresolved.")
        self.night_check.setVisible(KIND_MAP in self._kinds)
        self.night_check.toggled.connect(self._load_content_async)
        layout.addWidget(self.night_check)
        self.chunk_combo = QComboBox()
        self.chunk_combo.addItem("All geometry chunks", None)
        self.chunk_combo.setVisible(KIND_MAP in self._kinds)
        self.chunk_combo.setToolTip("Inspect one ROM geometry chunk. Game portal visibility is not simulated.")
        self.chunk_combo.currentIndexChanged.connect(self._texture_frame_changed)
        layout.addWidget(self.chunk_combo)
        self.objects_list = QListWidget()
        self.objects_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.objects_list.setMaximumHeight(170)
        self.objects_list.setVisible(KIND_MAP in self._kinds)
        layout.addWidget(self.objects_list)
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 255)
        self.frame_spin.setPrefix("Texture frame ")
        self.frame_spin.setToolTip("Select a ROM texture frame. Each slot wraps independently; Kong blinking is available in Characters; gameplay timing is not inferred for generic actors.")
        self.frame_spin.valueChanged.connect(self._texture_frame_changed)
        layout.addWidget(self.frame_spin)
        self.fog_check = QCheckBox("Map fog (ROM settings)")
        self.fog_check.setChecked(False)
        self.fog_check.setVisible(KIND_MAP in self._kinds)
        self.fog_check.setToolTip("Original colour and projected-depth range. Optional: the distant overview camera can make original fog very dense.")
        self.fog_check.toggled.connect(self._update_fog)
        layout.addWidget(self.fog_check)
        self.reset_button = QPushButton("Reset view")
        self.export_button = QPushButton("Export current view / pose...")
        self.export_button.setEnabled(False)
        self.export_all_button = QPushButton("Export all shown as GLB...")
        self.export_all_button.setToolTip("Export every entry of the current list (search and filter apply) "
                                          "as static GLB files into one folder.")
        layout.addWidget(self.reset_button)
        layout.addWidget(self.export_button)
        self.export_selected_button = QPushButton("Export selected objects...")
        self.export_selected_button.setVisible(KIND_MAP in self._kinds)
        self.export_selected_button.clicked.connect(self._export_selected)
        layout.addWidget(self.export_selected_button)
        layout.addWidget(self.export_all_button)
        note = QLabel("Confirmed actor clips are preferred; compatible-only clips remain exploratory. "
                      "ROM colour/alpha combiner; preview lighting. "
                      "Magenta = texture referenced but not decoded. Texture frames are selectable.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search_edit.textChanged.connect(self._refresh_list)
        self.kind_combo.currentIndexChanged.connect(self._refresh_list)
        self.list_widget.currentItemChanged.connect(self._select_item)
        self.reset_button.clicked.connect(lambda: self.viewport and self.viewport.reset_view())
        self.export_all_button.clicked.connect(lambda _checked=False: self.export_all())
        self.export_button.clicked.connect(self._export_current)
        return panel

    def pause(self):
        self._scene_timer.stop()
        self.scene_play.setText("Play")

    def _export_actor_clip(self):
        if self._actor_animations is None or self._current is None:
            return
        entry, model = self._current
        target, _ = QFileDialog.getSaveFileName(self, "Export actor and compatible clip", safe_file_stem(entry) + "_animated.glb", "Binary glTF (*.glb)")
        if not target:
            return
        try:
            result = self._actor_animations.export_glb(model, Path(target), entry.name)
            self.status_message.emit(f"Exported {result['samples']} samples on {result['bones']} bones; preview timing, ownership recorded in GLB.")
        except Exception as exc:
            QMessageBox.warning(self, "Actor clip export failed", str(exc))

    def _load_actor_clips(self):
        if self._current is None:
            return
        if self._current[0].kind == KIND_PROP:
            from .core import prop_animation
            rig = prop_animation.parse(texture_bank.table_entry(self._rom, 4, self._current[0].index))
            self.clip_combo.blockSignals(True)
            self.clip_combo.clear()
            self.clip_combo.addItem("Static rest pose", None)
            for track in rig.tracks if rig else ():
                self.clip_combo.addItem(f"ROM track {track.index}: {len(track.speeds)} keys, {len(track.rows)} matrices", track.index)
            self.clip_combo.blockSignals(False)
            self.status_message.emit("Embedded prop tracks; script speed and triggering are preview controls.")
            return
        if self._current[0].kind != KIND_ACTOR:
            return
        from .actor_animation import ActorAnimations, animation_assets
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if self._animation_assets is None:
                self._animation_assets = animation_assets(self._rom)
            entry, model = self._current
            if self._actor_animations is None:
                self._actor_animations = ActorAnimations(self._rom, entry.index, model, self._animation_assets)
            self.clip_combo.blockSignals(True)
            self.clip_combo.clear()
            self.clip_combo.addItem("Static rest pose", None)
            for descriptor in self._actor_animations.descriptors:
                self.clip_combo.addItem(descriptor.label, descriptor.table11_id)
            self.clip_combo.blockSignals(False)
            self.status_message.emit(f"{sum(d.owned for d in self._actor_animations.descriptors)} source-confirmed, {sum(not d.owned for d in self._actor_animations.descriptors)} compatible-only clips; preview timing.")
        except Exception as exc:
            self._actor_animations = None
            self.status_message.emit(f"Actor animation unavailable: {exc}")
        finally:
            self.clip_combo.blockSignals(False)
            QGuiApplication.restoreOverrideCursor()

    def _select_actor_clip(self, *_args):
        self._scene_timer.stop()
        self.scene_play.setText("Play")
        self.clip_export.setEnabled(False)
        index = self.clip_combo.currentData()
        if self._current is not None and self._current[0].kind == KIND_PROP:
            self._scene_tick = 0
            self.clip_frame.setRange(0, 6000 if index is not None else 0)
            self.clip_frame.setValue(0)
            self._show_actor_frame(0)
            return
        if self._actor_animations is None or self._current is None:
            return
        if index is None:
            self._show_render(self._current[1])
            return
        try:
            descriptor = self._actor_animations.select(index)
        except Exception as exc:
            self.status_message.emit(f"Clip rejected by complete interior sampling: {exc}")
            self.clip_frame.setRange(0, 0)
            return
        self.clip_frame.setRange(0, descriptor.sample_count - 1)
        self.clip_frame.setValue(0)
        self._show_actor_frame(0)
        self.clip_export.setEnabled(True)

    def _show_actor_frame(self, frame):
        if self._current is not None and self._current[0].kind == KIND_PROP:
            entry, _model = self._current
            model = static_model.prop_model(self._rom, entry.index, self._cache, tick=frame,
                                           track=self.clip_combo.currentData(), speed=self.prop_speed.value())
            if model is not None:
                camera = self.viewport._camera
                self.viewport.set_model_data(model.render, static_model.marker_skeleton(model.render))
                self.viewport._camera = camera
                self._current = entry, model
            return
        if self._actor_animations is None or not self._actor_animations.samples or self.clip_combo.currentData() is None:
            return
        points = self._actor_animations.pose(frame)
        self.viewport.set_scene_data(points, static_model.marker_skeleton(self._current[1].render))
        self.viewport.set_vertex_colors(self._actor_animations.colors(frame))

    def _toggle_scene_play(self):
        if self._scene_timer.isActive():
            self._scene_timer.stop()
            self.scene_play.setText("Play")
        elif self._current is not None:
            self._start_scene_playback()
            self.scene_play.setText("Pause")

    def _start_scene_playback(self):
        self._playback_time = time.perf_counter()
        self._playback_remainder = 0.0
        self._scene_timer.start()

    def _playback_frame(self):
        now = time.perf_counter()
        elapsed = 0 if self._playback_time is None else now - self._playback_time
        self._playback_time = now
        self._playback_remainder += elapsed * 30.0
        steps = int(self._playback_remainder)
        self._playback_remainder -= steps
        if steps:
            self._advance_scene(steps)
        elif self.viewport is not None:
            self.viewport.update()

    def _advance_scene(self, steps=1):
        if self._current is None:
            self._scene_timer.stop()
            return
        entry, old = self._current
        if entry.kind == KIND_PROP and self.clip_combo.currentData() is not None:
            self.clip_frame.setValue((self.clip_frame.value() + steps) % (self.clip_frame.maximum() + 1))
            return
        if entry.kind == KIND_ACTOR:
            if self._actor_animations is not None and self._actor_animations.samples and self.clip_combo.currentData() is not None:
                self.clip_frame.setValue((self.clip_frame.value() + steps) % len(self._actor_animations.samples))
            return
        self._scene_tick += steps
        if entry.kind == KIND_MAP:
            from dataclasses import replace
            from .level_playback import map_playback
            if self._map_playback is None:
                base = static_model.map_model(self._rom, entry.index, self._cache,
                    chunks=None if self.chunk_combo.currentData() is None else {self.chunk_combo.currentData()})
                self._map_playback = map_playback(self._rom, entry.index, base, self._cache)
            render = self._map_playback.render(self._scene_tick)
            self.viewport.set_dynamic_render(render)
            self._current = entry, replace(old, render=render)
            if self._content_playback is not None and self.content_check.isChecked():
                self._content = self._content_playback.render(self._scene_tick)
                if self._actor_playback is not None:
                    self._content = self._actor_playback.render(self._content, self._scene_tick)
                self.viewport.set_dynamic_render(self._content, attachment=True)
            return
        model = LOADERS[entry.kind](self._rom, entry.index, self._cache, tick=self._scene_tick,
                                          **({"chunks": None if self.chunk_combo.currentData() is None else {self.chunk_combo.currentData()}} if entry.kind == KIND_MAP else {}))
        if model is not None:
            camera = self.viewport._camera
            if model.render.positions == old.render.positions and model.render.batches == old.render.batches:
                self.viewport.set_textures(model.render.textures)
            else:
                self.viewport.set_model_data(model.render, static_model.marker_skeleton(model.render))
                self.viewport._camera = camera
            self._current = entry, model
            if entry.kind == KIND_MAP and self.content_check.isChecked():
                from .level_content import content_render
                self._content, _rows, _missing = content_render(self._rom, entry.index, self._cache,
                    tick=self._scene_tick, night=self.night_check.isChecked(), models=self._content_models, capture=self._captured)
                self.viewport.set_attachment_data(self._content)

    def _prepare_content(self, entry, night, progress):
        from .level_content import content_render
        from .level_playback import content_playback
        from .level_actors import ActorPlayback
        captured = []
        progress("Loading placed objects and actor models")
        content, rows, missing = content_render(self._rom, entry.index, self._cache,
            night=night, models=self._content_models, capture=captured)
        progress("Preparing confirmed actor animations")
        playback = content_playback(content, self._content_models, self._cache) if content else None
        actors = ActorPlayback(self._rom, captured, self._content_models) if content else None
        return content, rows, missing, captured, playback, actors

    def _load_content_async(self):
        if self._current is None or not self.content_check.isChecked():
            self._loader.cancel()
            self._level_content_changed()
            return
        self.pause()
        entry = self._current[0]
        night = self.night_check.isChecked()
        self._loader.submit(lambda progress: self._prepare_content(entry, night, progress),
            lambda result: self._level_content_changed(prepared=result))

    def _level_content_changed(self, *_args, prepared=None):
        self._content_playback = None
        self._content = None
        self._actor_playback = None
        self._captured = []
        self.objects_list.clear()
        if self.viewport is None or self._current is None:
            return
        self.viewport.set_attachment_data(None)
        entry, _model = self._current
        if entry.kind != KIND_MAP or not self.content_check.isChecked():
            return
        from . import level_content
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if prepared is None:
                prepared = self._prepare_content(entry, self.night_check.isChecked(), self.status_message.emit)
            self._content, rows, missing, self._captured, self._content_playback, self._actor_playback = prepared
        except Exception as exc:
            self.status_message.emit(f"Level contents could not be read: {exc}")
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        for row in rows:
            xyz = ", ".join(f"{v:.1f}" for v in row.position)
            item = QListWidgetItem(f"{row.kind} {row.index}: type {row.type_id:03X}, id {row.object_id}, ({xyz})")
            item.setData(Qt.ItemDataRole.UserRole, (row.kind, row.index))
            self.objects_list.addItem(item)
        self.viewport.set_attachment_data(self._content)
        animated = len(self._actor_playback.instances) if self._actor_playback else 0
        self.status_message.emit(f"{len(rows)} objects/spawns; {animated} with confirmed clips; {len(missing)} markers. "
                                 "Actors use the game's model tables; spawn conditions and scripts are not executed.")

    def _texture_frame_changed(self, *_args):
        self._map_playback = None
        if self._current is None:
            return
        entry, _old = self._current
        model = LOADERS[entry.kind](self._rom, entry.index, self._cache, frame=self.frame_spin.value(),
                                          **({"chunks": None if self.chunk_combo.currentData() is None else {self.chunk_combo.currentData()}} if entry.kind == KIND_MAP else
                                             {"track": self.clip_combo.currentData(), "tick": self.clip_frame.value(),
                                              "speed": self.prop_speed.value(), "texture_playback": False} if entry.kind == KIND_PROP else {}))
        if model is not None:
            self._current = entry, model
            camera = self.viewport._camera
            self.viewport.set_model_data(model.render, static_model.marker_skeleton(model.render))
            self.viewport._camera = camera

    # ------------------------------------------------------------------ loading
    def ensure_loaded(self) -> None:
        if self.loaded:
            return
        self.loaded = True
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._entries = catalog(self._rom, self._kinds)
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.status_message.emit(f"{self._title} — {len(self._entries):,} entries")
        self._refresh_list()
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def shown(self):
        return filter_entries(self._entries, self.search_edit.text(), str(self.kind_combo.currentData()))

    def _refresh_list(self, *_args) -> None:
        if not self.loaded:
            return
        keep = None if self._current is None else self._current[0]
        rows = self.shown()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        selected = -1
        for row, entry in enumerate(rows):
            item = QListWidgetItem(entry.label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.list_widget.addItem(item)
            if entry == keep:
                selected = row
        if selected >= 0:
            self.list_widget.setCurrentRow(selected)
        self.list_widget.blockSignals(False)
        self.count_label.setText(f"{len(rows):,} of {len(self._entries):,} entries")

    def _select_item(self, item, _previous=None) -> None:
        if item is not None:
            entry = item.data(Qt.ItemDataRole.UserRole)
            self.pause()
            self._loader.submit(lambda progress: self._prepare_entry(entry, progress),
                                lambda result: self.show_entry(entry, prepared=result))

    def _prepare_entry(self, entry, progress):
        key = entry.kind, entry.index
        if key in self._assets:
            self._assets.move_to_end(key)
            return self._assets[key]
        progress(f"Loading {entry.label}: geometry and textures")
        model = LOADERS[entry.kind](self._rom, entry.index, self._cache)
        animation = None
        if model and entry.kind == KIND_ACTOR:
            from .actor_animation import ActorAnimations, animation_assets
            progress(f"Loading {entry.label}: animation catalog")
            if self._animation_assets is None:
                self._animation_assets = animation_assets(self._rom)
            try:
                animation = ActorAnimations(self._rom, entry.index, model, self._animation_assets)
            except ValueError:
                animation = None
        result = model, animation
        self._assets[key] = result
        if len(self._assets) > 12:
            self._assets.popitem(last=False)
        return result

    def show_entry(self, entry: BrowserEntry, *, prepared=None) -> static_model.StaticModel | None:
        if prepared is None:
            self._loader.cancel()
        self._scene_timer.stop()
        self.scene_play.setText("Play")
        self._scene_tick = 0
        self._map_playback = None
        self._content_playback = None
        # Reuse decoded placement assets when revisiting levels.
        self.chunk_combo.blockSignals(True)
        self.chunk_combo.clear()
        self.chunk_combo.addItem("All geometry chunks", None)
        if entry.kind == KIND_MAP:
            from .core.mesh_decoder import map_ranges
            data = texture_bank.table_entry(self._rom, 1, entry.index)
            for chunk in sorted({row[3] for row in map_ranges(data, with_chunk=True)}):
                self.chunk_combo.addItem(f"Geometry chunk {chunk}", chunk)
        self.chunk_combo.blockSignals(False)
        self.night_check.setEnabled(entry.kind == KIND_MAP and entry.index == 48)
        self._actor_animations = None
        self.clip_combo.blockSignals(True)
        self.clip_combo.clear()
        self.clip_combo.addItem("Static rest pose", None)
        self.clip_combo.blockSignals(False)
        self.clip_frame.setRange(0, 0)
        self.load_clips_button.setEnabled(entry.kind in (KIND_ACTOR, KIND_PROP))
        self.load_clips_button.setText("Read embedded prop tracks" if entry.kind == KIND_PROP else "Find compatible actor clips")
        self.clip_export.setEnabled(False)
        self.prop_speed.setEnabled(entry.kind == KIND_PROP)
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if prepared is None:
                prepared = self._prepare_entry(entry, self.status_message.emit)
            model, self._actor_animations = prepared
        except Exception as exc:  # keep browsing when one entry has an unexpected layout
            model, error = None, str(exc)
        else:
            error = "no decodable display list"
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.name_value.setText(entry.name)
        self.source_value.setText(entry.name_source)
        if model is None or model.triangles == 0:
            self._current = None
            self.export_button.setEnabled(False)
            self.geometry_value.setText("-")
            self.texture_value.setText("-")
            self.notes_value.setText(f"Not shown: {error if model is None else 'no triangles'}.")
            return model
        self._current = (entry, model)
        self.export_button.setEnabled(True)
        self.geometry_value.setText(f"{model.triangles:,} triangles, {len(model.render.batches)} batches")
        textured = model.textured_triangles / model.triangles
        missing = f", {model.missing_textures} not decoded" if model.missing_textures else ""
        self.texture_value.setText(f"{model.textures} images{missing} · {textured:.0%} of triangles textured")
        self.notes_value.setText(", ".join(f"{name}: {count}" for name, count in model.unsupported.items())
                                 or "-")
        self._show_render(model)
        if entry.kind == KIND_MAP:
            from .level_playback import map_playback
            self._map_playback = map_playback(self._rom, entry.index, model, self._cache)
        self._update_fog()
        if self.content_check.isChecked():
            self._load_content_async()
        else:
            self._level_content_changed()
        self._autoplay_model()
        self.list_widget.blockSignals(True)
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == entry:
                self.list_widget.setCurrentItem(item)
                self.list_widget.scrollToItem(item)
                break
        self.list_widget.blockSignals(False)
        return model

    def _autoplay_model(self):
        """Play embedded prop tracks or the first fully validated actor candidate."""
        if self._current is None or self._current[0].kind == KIND_MAP:
            return
        self._load_actor_clips()
        for index in range(1, self.clip_combo.count()):
            self.clip_combo.setCurrentIndex(index)
            if (self._current[0].kind == KIND_PROP or
                    (self._actor_animations is not None and self._actor_animations.samples)):
                self._start_scene_playback()
                self.scene_play.setText("Pause")
                return
        if self.clip_combo.currentData() is not None:
            self.clip_combo.setCurrentIndex(0)
        # Props without matrices can still have embedded texture animation.
        if self._current[0].kind == KIND_PROP:
            from .core import texture_animation
            data = texture_bank.table_entry(self._rom, 4, self._current[0].index)
            if texture_animation.prop_animations(data):
                self._start_scene_playback()
                self.scene_play.setText("Pause")

    def _update_fog(self):
        if self.viewport is None or self._current is None:
            return
        entry = self._current[0]
        data = texture_bank.table_entry(self._rom, 1, entry.index) if entry.kind == KIND_MAP else b""
        enabled = len(data) > 8 and bool(data[8] & 1) and self.fog_check.isChecked()
        self.viewport.fog = ((138/255, 82/255, 22/255) if entry.index == 38 else (0.,0.,0.)) if enabled else None
        self.viewport.update()

    def _show_render(self, model: static_model.StaticModel) -> None:
        skeleton = static_model.marker_skeleton(model.render)
        if self.viewport is None:
            from .viewport import ModelViewport
            self.viewport = ModelViewport(model.render, skeleton)
            self.viewport.set_view_mode(ViewMode.MESH)
            self._placeholder.hide()
            self._view_layout.addWidget(self.viewport)
        else:
            self.viewport.set_model_data(model.render, skeleton)

    def export_jobs(self, entries=None, *, with_content: bool | None = None):
        """(label, job) pairs exporting each entry as a static GLB (maps optionally with
        their placed props/actors, as the content checkbox shows them)."""
        if entries is None:
            entries = self.shown()
        if with_content is None:
            with_content = self.content_check.isChecked()

        def job_for(entry):
            def job(folder: Path):
                model = LOADERS[entry.kind](self._rom, entry.index, self._cache)
                if model is None or model.triangles == 0:
                    raise ValueError("no decodable geometry")
                if entry.kind == KIND_MAP and with_content:
                    from dataclasses import replace
                    from . import level_content
                    content, _rows, _missing = level_content.content_render(self._rom, entry.index, self._cache, night=self.night_check.isChecked())
                    if content is not None:
                        render = level_content.merge_render((model.render, content))
                        model = replace(model, render=render, triangles=sum(b.face_count for b in render.batches),
                                        textures=len(render.textures))
                static_model.export_glb(model, folder / f"{safe_file_stem(entry)}.glb", safe_file_stem(entry))
            return job
        return [(entry.label, job_for(entry)) for entry in entries]

    def export_all(self, folder: Path | None = None):
        from .batch_export import export_all
        self.ensure_loaded()
        result = export_all(self, f"Export {self._title}", self.export_jobs(), folder)
        if result is not None:
            self.status_message.emit(result.summary())
        return result

    def _export_current(self):
        if self._current is None:
            return
        self.pause()
        target, _ = QFileDialog.getSaveFileName(self, "Export current view",
            safe_file_stem(self._current[0]) + "_pose.glb", "Binary glTF (*.glb);;Preview image (*.png)")
        if target:
            from .snapshot import export_view
            try:
                export_view(self.viewport, Path(target))
                self.status_message.emit(f"Exported {Path(target).name}")
            except Exception as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def _export_selected(self):
        selected = {i.data(Qt.ItemDataRole.UserRole) for i in self.objects_list.selectedItems()}
        if not selected or self._content is None:
            self.status_message.emit("Select objects in the list first (Ctrl / Shift for multiple).")
            return
        self.pause()
        target, _ = QFileDialog.getSaveFileName(self, "Export selected objects", "selected_objects.glb", "Binary glTF (*.glb)")
        if not target:
            return
        from dataclasses import replace
        from .level_content import merge_render
        from .level_actors import ActorPlayback
        captured = [(row, scene) for row, scene in self._captured if (row.kind, row.index) in selected]
        render = merge_render([scene for row, scene in captured])
        from .level_playback import content_playback
        render = content_playback(render, self._content_models, self._cache).render(self._scene_tick)
        render = ActorPlayback(self._rom, captured, self._content_models).render(render, self._scene_tick)
        from .snapshot import freeze_billboards
        render = freeze_billboards(render, self.viewport._camera)
        model = replace(self._current[1], render=render, triangles=sum(b.face_count for b in render.batches))
        try:
            static_model.export_glb(model, Path(target), "Selected objects", camera=self.viewport._camera)
            self.status_message.emit(f"Exported {len(selected)} selected objects")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
