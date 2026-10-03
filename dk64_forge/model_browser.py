"""Models and Levels tabs: browse every actor model, prop and map as a static, textured mesh.

Layout follows MIT-licensed jfg_forge.gui.prop_browser / level_browser (Copyright 2026
Marvelmaster): searchable list, viewport, info panel, static export. DK64 decoding is
dk64_forge.core.mesh_decoder via dk64_forge.static_model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QSlider, QSplitter, QVBoxLayout, QWidget,
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
        self._actor_animations = None
        self._animation_assets = None
        self._scene_tick = 0
        self._scene_timer = QTimer(self)
        self._scene_timer.setInterval(33)
        self._scene_timer.timeout.connect(self._advance_scene)
        self.loaded = False
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
            widget.setVisible(KIND_ACTOR in self._kinds)
        self.scene_play = QPushButton("Play")
        self.scene_play.clicked.connect(self._toggle_scene_play)
        layout.addWidget(self.scene_play)
        self.content_check = QCheckBox("Show placed props and actor spawn markers")
        self.content_check.setVisible(KIND_MAP in self._kinds)
        self.content_check.toggled.connect(self._level_content_changed)
        layout.addWidget(self.content_check)
        self.objects_list = QListWidget()
        self.objects_list.setMaximumHeight(170)
        self.objects_list.setVisible(KIND_MAP in self._kinds)
        layout.addWidget(self.objects_list)
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 255)
        self.frame_spin.setPrefix("Texture frame ")
        self.frame_spin.setToolTip("Select a ROM texture frame. Each slot wraps independently; game blink/timing scripts are not simulated.")
        self.frame_spin.valueChanged.connect(self._texture_frame_changed)
        layout.addWidget(self.frame_spin)
        self.reset_button = QPushButton("Reset view")
        self.export_button = QPushButton("Export static GLB...")
        self.export_button.setEnabled(False)
        layout.addWidget(self.reset_button)
        layout.addWidget(self.export_button)
        note = QLabel("Static rest pose. ROM colour/alpha combiner; preview lighting. "
                      "Magenta = texture referenced but not decoded. Texture frames are selectable.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search_edit.textChanged.connect(self._refresh_list)
        self.kind_combo.currentIndexChanged.connect(self._refresh_list)
        self.list_widget.currentItemChanged.connect(self._select_item)
        self.reset_button.clicked.connect(lambda: self.viewport and self.viewport.reset_view())
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
            self.status_message.emit(f"Exported {result['samples']} samples on {result['bones']} bones; diagnostic timing, owner unknown.")
        except Exception as exc:
            QMessageBox.warning(self, "Actor clip export failed", str(exc))

    def _load_actor_clips(self):
        if self._current is None or self._current[0].kind != KIND_ACTOR:
            return
        from .actor_animation import ActorAnimations, animation_assets
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if self._animation_assets is None:
                self._animation_assets = animation_assets(self._rom)
            entry, model = self._current
            self._actor_animations = ActorAnimations(self._rom, entry.index, model, self._animation_assets)
            self.clip_combo.blockSignals(True)
            self.clip_combo.clear()
            self.clip_combo.addItem("Static rest pose", None)
            for descriptor in self._actor_animations.descriptors:
                self.clip_combo.addItem(descriptor.label, descriptor.table11_id)
            self.clip_combo.blockSignals(False)
            self.status_message.emit(f"{len(self._actor_animations.descriptors)} structurally compatible clips; owner, game timing and runtime adjustments remain unverified.")
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
        if self._actor_animations is None or not self._actor_animations.samples or self.clip_combo.currentData() is None:
            return
        points = self._actor_animations.pose(frame)
        self.viewport.set_scene_data(points, static_model.marker_skeleton(self._current[1].render))

    def _toggle_scene_play(self):
        if self._scene_timer.isActive():
            self._scene_timer.stop()
            self.scene_play.setText("Play")
        elif self._current is not None:
            self._scene_timer.start()
            self.scene_play.setText("Pause")

    def _advance_scene(self):
        if self._current is None:
            self._scene_timer.stop()
            return
        entry, old = self._current
        if entry.kind == KIND_ACTOR:
            if self._actor_animations is not None and self._actor_animations.samples and self.clip_combo.currentData() is not None:
                self.clip_frame.setValue((self.clip_frame.value() + 1) % len(self._actor_animations.samples))
            return
        self._scene_tick += 1
        model = LOADERS[entry.kind](self._rom, entry.index, self._cache, tick=self._scene_tick)
        if model is not None:
            camera = self.viewport._camera
            if model.render.positions == old.render.positions and model.render.batches == old.render.batches:
                self.viewport.set_textures(model.render.textures)
            else:
                self.viewport.set_model_data(model.render, static_model.marker_skeleton(model.render))
                self.viewport._camera = camera
            self._current = entry, model

    def _level_content_changed(self, *_args):
        self._content = None
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
            self._content, rows, missing = level_content.content_render(self._rom, entry.index, self._cache)
        except Exception as exc:
            self.status_message.emit(f"Level contents could not be read: {exc}")
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        for row in rows:
            xyz = ", ".join(f"{v:.1f}" for v in row.position)
            self.objects_list.addItem(f"{row.kind} {row.index}: type {row.type_id:03X}, id {row.object_id}, ({xyz})")
        self.viewport.set_attachment_data(self._content)
        self.status_message.emit(f"{len(rows)} placed objects/spawns; {len(missing)} shown as markers (no model). "
                                 "Actors use the game's model tables; spawn conditions and scripts are not executed.")

    def _texture_frame_changed(self, *_args):
        if self._current is None:
            return
        entry, _old = self._current
        model = LOADERS[entry.kind](self._rom, entry.index, self._cache, frame=self.frame_spin.value())
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
            self.show_entry(item.data(Qt.ItemDataRole.UserRole))

    def show_entry(self, entry: BrowserEntry) -> static_model.StaticModel | None:
        self._scene_timer.stop()
        self.scene_play.setText("Play")
        self._scene_tick = 0
        self._actor_animations = None
        self.clip_combo.blockSignals(True)
        self.clip_combo.clear()
        self.clip_combo.addItem("Static rest pose", None)
        self.clip_combo.blockSignals(False)
        self.clip_frame.setRange(0, 0)
        self.load_clips_button.setEnabled(entry.kind == KIND_ACTOR)
        self.clip_export.setEnabled(False)
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            model = LOADERS[entry.kind](self._rom, entry.index, self._cache)
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
        self._level_content_changed()
        return model

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

    def _export_current(self) -> None:
        if self._current is None:
            return
        entry, model = self._current
        if self._content is not None:
            from dataclasses import replace
            from .level_content import merge_render
            render = merge_render((model.render, self._content))
            model = replace(model, render=render, triangles=sum(b.face_count for b in render.batches), textures=len(render.textures))
        target, _ = QFileDialog.getSaveFileName(self, "Export static GLB",
                                                f"{safe_file_stem(entry)}.glb", "Binary glTF (*.glb)")
        if not target:
            return
        try:
            result = static_model.export_glb(model, Path(target), safe_file_stem(entry))
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.status_message.emit(f"Exported {Path(target).name}: {result['triangles']:,} triangles, "
                                 f"{result['textures']} textures")
