"""Models and Levels tabs: browse every actor model, prop and map as a static, textured mesh.

Layout follows MIT-licensed jfg_forge.gui.prop_browser / level_browser (Copyright 2026
Marvelmaster): searchable list, viewport, info panel, static export. DK64 decoding is
dk64_forge.core.mesh_decoder via dk64_forge.static_model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
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
        self.loaded = False
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_side_panel())
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
        self.reset_button = QPushButton("Reset view")
        self.export_button = QPushButton("Export static GLB...")
        self.export_button.setEnabled(False)
        layout.addWidget(self.reset_button)
        layout.addWidget(self.export_button)
        note = QLabel("Static rest pose. Lighting and the colour combiner are approximated "
                      "(texture × vertex colour); magenta = texture referenced but not decoded.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search_edit.textChanged.connect(self._refresh_list)
        self.kind_combo.currentIndexChanged.connect(self._refresh_list)
        self.list_widget.currentItemChanged.connect(self._select_item)
        self.reset_button.clicked.connect(lambda: self.viewport and self.viewport.reset_view())
        self.export_button.clicked.connect(self._export_current)
        return panel

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
