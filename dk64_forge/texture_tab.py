"""Textures tab: browse, inspect and export the ROM's table-25 geometry textures.

Layout and behaviour follow MIT-licensed jfg_forge.gui.texture_tab (Copyright 2026
Marvelmaster): search, filters, sorting, a pixel preview, derived names and PNG
export. The DK64 texture bank itself (formats from display-list usage, names from
users) is dk64_forge.core.texture_bank.
"""

from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from .core import texture_bank

SHOW_ALL, SHOW_DECODED, SHOW_UNDECODED = "All textures", "Decoded", "Not decoded"
SHOW_USED, SHOW_UNUSED, SHOW_PALETTES = "Used by a model or map", "Unused (no reference found)", "Palettes (TLUT)"
SORT_NUMBER, SORT_NAME, SORT_SIZE = "Number", "Name", "Byte size"


def filter_items(items, text: str = "", show: str = SHOW_ALL, sort: str = SORT_NUMBER,
                 decodable=None):
    """Pure list filter (tested without Qt)."""
    needle = text.strip().lower()
    rows = []
    for item in items:
        if needle and not (needle in item.name.lower() or needle == str(item.index)
                           or needle == f"{item.index:x}" or needle == f"0x{item.index:x}"
                           or any(needle in user.lower() for user in item.users)):
            continue
        if show == SHOW_USED and item.kind != "used":
            continue
        if show == SHOW_UNUSED and item.kind != "unused":
            continue
        if show == SHOW_PALETTES and item.kind != "palette":
            continue
        if show in (SHOW_DECODED, SHOW_UNDECODED) and decodable is not None:
            if decodable(item) != (show == SHOW_DECODED):
                continue
        rows.append(item)
    key = {SORT_NAME: lambda i: (i.name.lower(), i.index),
           SORT_SIZE: lambda i: (-i.byte_size, i.index)}.get(sort, lambda i: i.index)
    return sorted(rows, key=key)


def safe_file_stem(item) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", item.name).strip("_")
    return f"T{item.index:04X}_{name}"[:80]


class TextureTab(QWidget):
    status_message = Signal(str)

    def __init__(self, rom: bytes, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rom = rom
        self._items: list[texture_bank.BankItem] = []
        self._decodable: dict[int, bool] = {}
        self._current: texture_bank.BankItem | None = None
        self._current_rgba: tuple[int, int, bytes] | None = None
        self.loaded = False
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_side_panel())
        splitter.addWidget(self._build_view())
        splitter.setSizes([360, 820])
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def _build_side_panel(self) -> QWidget:
        panel = QFrame()
        panel.setMinimumWidth(280)
        layout = QVBoxLayout(panel)
        heading = QLabel("Texture bank (table 25)")
        heading.setStyleSheet("font-weight: bold; font-size: 15px;")
        layout.addWidget(heading)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search name, number (decimal or 0x hex) or user")
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)
        form = QFormLayout()
        self.show_combo = QComboBox()
        for label in (SHOW_ALL, SHOW_DECODED, SHOW_UNDECODED, SHOW_USED, SHOW_UNUSED, SHOW_PALETTES):
            self.show_combo.addItem(label, label)
        self.sort_combo = QComboBox()
        for label in (SORT_NUMBER, SORT_NAME, SORT_SIZE):
            self.sort_combo.addItem(label, label)
        form.addRow("Show", self.show_combo)
        form.addRow("Sort by", self.sort_combo)
        layout.addLayout(form)
        self.count_label = QLabel("Open the tab to scan the texture bank.")
        layout.addWidget(self.count_label)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, stretch=1)
        info = QFormLayout()
        self.name_value = QLabel("-")
        self.name_value.setWordWrap(True)
        self.id_value = QLabel("-")
        self.size_value = QLabel("-")
        self.format_value = QLabel("-")
        self.format_value.setWordWrap(True)
        self.used_value = QLabel("-")
        self.used_value.setWordWrap(True)
        for label, widget in (("Name (derived)", self.name_value), ("Texture", self.id_value),
                              ("Size", self.size_value), ("Format", self.format_value),
                              ("Used by", self.used_value)):
            info.addRow(label, widget)
        layout.addLayout(info)
        note = QLabel("The ROM stores no texture names or formats. Formats come from how a model or "
                      "map loads the texture; names from the first user.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search_edit.textChanged.connect(self._refresh_list)
        self.show_combo.currentIndexChanged.connect(self._refresh_list)
        self.sort_combo.currentIndexChanged.connect(self._refresh_list)
        self.list_widget.currentItemChanged.connect(self._select_item)
        return panel

    def _build_view(self) -> QWidget:
        area = QFrame()
        layout = QVBoxLayout(area)
        self.image_label = QLabel("Select a texture from the list.")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setWordWrap(True)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.image_label)
        layout.addWidget(scroll, stretch=1)
        self.guess_check = QCheckBox("Preview unreferenced data as RGBA16 rows of 32 texels (GUESS)")
        self.export_button = QPushButton("Export PNG...")
        self.export_all_button = QPushButton("Export shown list as PNG files...")
        for widget in (self.guess_check, self.export_button, self.export_all_button):
            layout.addWidget(widget)
        self.export_button.setEnabled(False)
        self.export_all_button.setEnabled(False)
        self.guess_check.toggled.connect(lambda _on: self._current and self.show_item(self._current))
        self.export_button.clicked.connect(self._export_current)
        self.export_all_button.clicked.connect(self._export_shown)
        return area

    # ------------------------------------------------------------------ loading
    def ensure_loaded(self) -> None:
        if self.loaded:
            return
        self.loaded = True
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._items = texture_bank.build_bank_items(self._rom)
        finally:
            QGuiApplication.restoreOverrideCursor()
        used = sum(1 for item in self._items if item.kind == "used")
        self.status_message.emit(
            f"Textures — {len(self._items):,} textures, {used:,} with a known format from model/map usage")
        self.export_all_button.setEnabled(True)
        self._refresh_list()
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _is_decodable(self, item) -> bool:
        if item.index not in self._decodable:
            self._decodable[item.index] = texture_bank.decode_item(self._rom, item) is not None
        return self._decodable[item.index]

    # ------------------------------------------------------------------ list
    def shown(self):
        show = str(self.show_combo.currentData())
        needs_decode = show in (SHOW_DECODED, SHOW_UNDECODED)
        return filter_items(self._items, self.search_edit.text(), show,
                            str(self.sort_combo.currentData()),
                            self._is_decodable if needs_decode else None)

    def _refresh_list(self, *_args) -> None:
        if not self.loaded:
            return
        keep = None if self._current is None else self._current.index
        rows = self.shown()
        self.list_widget.blockSignals(True)
        self.list_widget.setUpdatesEnabled(False)
        self.list_widget.clear()
        selected = -1
        for row, item in enumerate(rows):
            marker = {"palette": "   [palette]", "unused": "   [no reference]"}.get(item.kind, "")
            entry = QListWidgetItem(f"{item.index:04X}  {item.name}{marker}")
            entry.setData(Qt.ItemDataRole.UserRole, item.index)
            self.list_widget.addItem(entry)
            if item.index == keep:
                selected = row
        if selected >= 0:
            self.list_widget.setCurrentRow(selected)
        self.list_widget.setUpdatesEnabled(True)
        self.list_widget.blockSignals(False)
        self.count_label.setText(f"{len(rows):,} of {len(self._items):,} textures")

    def _select_item(self, entry: QListWidgetItem | None, _previous=None) -> None:
        if entry is None:
            return
        index = entry.data(Qt.ItemDataRole.UserRole)
        item = next((candidate for candidate in self._items if candidate.index == index), None)
        if item is not None:
            self.show_item(item)

    # ------------------------------------------------------------------ view
    def show_item(self, item) -> None:
        self._current = item
        self._set_info(item)
        guess = item.kind == "unused" and self.guess_check.isChecked()
        decoded = texture_bank.decode_item(self._rom, item, guess=guess)
        if decoded is None:
            self._current_rgba = None
            self.image_label.setPixmap(QPixmap())
            reason = ("no model or map loads it, so its format is unknown — enable the GUESS preview"
                      if item.kind == "unused" else "this format/size could not be decoded")
            self.image_label.setText(f"{item.name}: {reason}.")
            self.export_button.setEnabled(False)
            return
        width, height, rgba = decoded
        self._current_rgba = decoded
        image = QImage(rgba, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        zoom = max(1, min(16, 384 // max(width, height)))
        pixmap = QPixmap.fromImage(image).scaled(width * zoom, height * zoom,
                                                 Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.FastTransformation)
        self.image_label.setText("")
        self.image_label.setPixmap(pixmap)
        self.export_button.setEnabled(True)
        self.status_message.emit(f"Textures / {item.index:04X} — {item.name}")

    def _set_info(self, item) -> None:
        self.name_value.setText(item.name)
        self.id_value.setText(f"table 25, entry {item.index} (0x{item.index:04X})")
        usage = item.usage
        if usage is not None:
            self.size_value.setText(f"{usage.width} × {usage.height} · {item.byte_size:,} bytes")
            notes = []
            if usage.interleaved:
                notes.append("dxt=0, odd rows de-interleaved")
            if usage.palette is not None:
                notes.append(f"palette 0x{usage.palette:04X}")
            if item.conflicting:
                notes.append("users load it with different sizes/formats; most common shown")
            self.format_value.setText(usage.format_name + (f" ({'; '.join(notes)})" if notes else ""))
        else:
            self.size_value.setText(f"{item.byte_size:,} bytes")
            self.format_value.setText("RGBA16 colour table (TLUT)" if item.is_palette
                                      else "UNKNOWN (no model or map loads it)")
        users = ", ".join(item.users[:8]) + (f" (+{len(item.users) - 8} more)" if len(item.users) > 8 else "")
        self.used_value.setText(users or ("palette of CI textures" if item.is_palette else "-"))

    # ------------------------------------------------------------------ export
    def _export_current(self) -> None:
        if self._current is None or self._current_rgba is None:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Export texture PNG",
                                                  safe_file_stem(self._current) + ".png", "PNG (*.png)")
        if filename:
            width, height, rgba = self._current_rgba
            Path(filename).write_bytes(texture_bank.rgba_png(width, height, rgba))
            self.status_message.emit(f"Exported {filename}")

    def _export_shown(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Export shown textures to folder")
        if not folder:
            return
        written = skipped = 0
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for item in self.shown():
                decoded = texture_bank.decode_item(self._rom, item)
                if decoded is None:
                    skipped += 1
                    continue
                width, height, rgba = decoded
                (Path(folder) / (safe_file_stem(item) + ".png")).write_bytes(
                    texture_bank.rgba_png(width, height, rgba))
                written += 1
        finally:
            QGuiApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Textures", f"Exported {written} PNG files ({skipped} not decodable).")
