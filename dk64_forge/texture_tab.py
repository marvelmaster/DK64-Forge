"""Textures tab: browse, inspect and export the ROM's table-25 geometry textures.

Layout and behaviour follow MIT-licensed jfg_forge.gui.texture_tab (Copyright 2026
Marvelmaster): search, filters, sorting, a pixel preview, derived names and PNG
export. The DK64 texture bank itself (formats from display-list usage, names from
users) is dk64_forge.core.texture_bank.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from PySide6.QtCore import QEvent, Qt, Signal, QSize, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap, QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from .core import texture_bank

SHOW_ALL, SHOW_DECODED, SHOW_UNDECODED = "All textures", "Decoded", "Not decoded"
SHOW_USED, SHOW_UNUSED, SHOW_PALETTES = "Used by a model or map", "Unused (no reference found)", "Palettes (TLUT)"
SORT_NUMBER, SORT_NAME, SORT_SIZE = "Number", "Name", "Byte size"


PREVIEW_SIZE = 384  # longest side of the preview in screen pixels


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
    return f"T{item.table}_{item.index:04X}_{name}"[:80]


class TextureTab(QWidget):
    status_message = Signal(str)
    open_usage = Signal(str, int)

    def __init__(self, rom: bytes, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rom = rom
        self._items: list[texture_bank.BankItem] = []
        self._decodable: dict[int, bool] = {}
        self._current: texture_bank.BankItem | None = None
        self._current_rgba: tuple[int, int, bytes] | None = None
        self.loaded = False
        from .background import Loader
        self._loader = Loader(self)
        self._loader.progress.connect(self.status_message)
        self._loader.failed.connect(self._load_failed)
        self._banks, self._sequences = {}, {}
        self._thumbnail_timer = QTimer(self)
        self._thumbnail_timer.setInterval(15)
        self._thumbnail_timer.timeout.connect(self._load_thumbnails)
        self._thumbnail_row = 0
        self._icons = {}
        self._sequence_timer = QTimer(self)
        self._sequence_timer.timeout.connect(self._sequence_next)
        self._sequence_frames = ()
        self._preview_zoom = 1.0
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
        heading = QLabel("Texture banks")
        heading.setStyleSheet("font-weight: bold; font-size: 15px;")
        layout.addWidget(heading)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search name, number (decimal or 0x hex) or user")
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)
        form = QFormLayout()
        self.bank_combo = QComboBox()
        for table, name in ((25, "25 · Geometry"), (7, "7 · Uncompressed"), (14, "14 · HUD")):
            self.bank_combo.addItem(name, table)
        self.bank_combo.currentIndexChanged.connect(self._bank_changed)
        form.addRow("Bank", self.bank_combo)
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
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        self._loader.busy.connect(self.progress_bar.setVisible)
        layout.addWidget(self.progress_bar)
        self.grid_check = QCheckBox("Thumbnail grid")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self._grid_changed)
        layout.addWidget(self.grid_check)
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setIconSize(QSize(64, 64))
        self.list_widget.setGridSize(QSize(92, 96))
        self.list_widget.setUniformItemSizes(True)
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
        info.labelForField(self.format_value).hide()
        self.format_value.hide()
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
        self.image_scroll = QScrollArea()
        self.image_scroll.setWidgetResizable(True)
        self.image_scroll.setWidget(self.image_label)
        self.image_scroll.viewport().installEventFilter(self)
        self.image_label.setToolTip("Scroll the mouse wheel to zoom in or out.")
        layout.addWidget(self.image_scroll, stretch=1)
        self.usage_list = QListWidget()
        self.usage_list.setMaximumHeight(100)
        self.usage_list.setToolTip("Double-click to open the model or level using this texture")
        self.usage_list.itemDoubleClicked.connect(self._open_usage)
        layout.addWidget(self.usage_list)
        self.sequence_combo = QComboBox()
        self.sequence_combo.currentIndexChanged.connect(self._sequence_changed)
        layout.addWidget(self.sequence_combo)
        self.sequence_frames = QListWidget()
        self.sequence_frames.setViewMode(QListWidget.ViewMode.IconMode)
        self.sequence_frames.setIconSize(QSize(48, 48))
        self.sequence_frames.setMaximumHeight(105)
        self.sequence_frames.currentRowChanged.connect(self._sequence_frame)
        layout.addWidget(self.sequence_frames)
        self.sequence_play = QPushButton("Play texture sequence")
        self.sequence_play.clicked.connect(self._sequence_toggle)
        layout.addWidget(self.sequence_play)
        self.manual_check = QCheckBox("Use manual decoding settings")
        self.manual_panel = QWidget()
        manual = QFormLayout(self.manual_panel)
        self.format_combo = QComboBox()
        for label, fmt, size in (("RGBA16", 0, 2), ("RGBA32", 0, 3), ("IA4", 3, 0), ("IA8", 3, 1), ("IA16", 3, 2), ("I4", 4, 0), ("I8", 4, 1)):
            self.format_combo.addItem(label, (fmt, size))
        self.width_spin, self.height_spin = QSpinBox(), QSpinBox()
        for spin in (self.width_spin, self.height_spin):
            spin.setRange(1, 2048)
            spin.setValue(32)
            spin.valueChanged.connect(self._manual_changed)
        self.interleave_check = QCheckBox("Undo odd-row word swap")
        for label, widget in (("Format", self.format_combo), ("Width", self.width_spin), ("Height", self.height_spin), ("Storage", self.interleave_check)):
            manual.addRow(label, widget)
        layout.addWidget(self.manual_panel)
        self.manual_panel.hide()
        self.manual_check.hide()
        self.manual_check.toggled.connect(self._manual_changed)
        self.format_combo.currentIndexChanged.connect(self._manual_changed)
        self.interleave_check.toggled.connect(self._manual_changed)
        self.guess_check = QCheckBox("Preview unreferenced data as RGBA16 rows of 32 texels (GUESS)")
        self.trilinear_check = QCheckBox("Trilinear filtering (smooth, mipmapped preview)")
        self.trilinear_check.setToolTip("Off: integer zoom showing exact texels. On: the texture is scaled "
                                        "to the preview size with mipmaps and bilinear interpolation "
                                        "(preview only; exports keep the original pixels).")
        self.export_button = QPushButton("Export PNG...")
        self.export_all_button = QPushButton("Export all shown as PNG...")
        for widget in (self.trilinear_check, self.guess_check, self.export_button, self.export_all_button):
            layout.addWidget(widget)
        self.guess_check.hide()
        self.export_button.setEnabled(False)
        self.export_all_button.setEnabled(False)
        self.guess_check.toggled.connect(lambda _on: self._current and self.show_item(self._current))
        self.trilinear_check.toggled.connect(lambda _on: self._current and self.show_item(self._current))
        self.export_button.clicked.connect(self._export_current)
        self.export_all_button.clicked.connect(self._export_shown)
        self._sequence_changed()
        return area

    # ------------------------------------------------------------------ loading
    def ensure_loaded(self):
        if self.loaded:
            return
        self.loaded = True
        table = self.bank_combo.currentData()
        def work(progress):
            if table not in self._banks:
                from .texture_sequences import sequences
                items = texture_bank.build_bank_items(self._rom,
                    lambda kind, index: progress(f"Scanning {kind} {index} for texture usage"), table=table)
                self._banks[table] = items, sequences(self._rom, table)
            return self._banks[table]
        self._loader.submit(work, self._bank_ready)

    def _load_failed(self, error):
        self.loaded = False
        self.count_label.setText("Loading failed; reopen this tab to retry.")
        self.status_message.emit(error)

    def _bank_ready(self, result):
        self._items, self._sequences = result
        self.export_all_button.setEnabled(True)
        self.status_message.emit(f"Loaded {len(self._items):,} textures and their usage/sequence references")
        self._refresh_list()
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _bank_changed(self, *_args) -> None:
        if not self.loaded:
            return
        self.loaded = False
        self._thumbnail_timer.stop()
        self._sequence_timer.stop()
        self.list_widget.clear()
        self._items = []
        self._decodable.clear()
        self._current = None
        self._current_rgba = None
        self.image_label.clear()
        self.image_label.setMinimumSize(0, 0)
        self.export_button.setEnabled(False)
        self.export_all_button.setEnabled(False)
        self.ensure_loaded()

    def _manual_changed(self, *_args) -> None:
        if self._current is not None:
            self.show_item(self._current)

    def _decode_current(self, item):
        if self.manual_check.isChecked():
            fmt, size = self.format_combo.currentData()
            usage = texture_bank.TextureUsage(fmt, size, self.width_spin.value(), self.height_spin.value(),
                                              self.interleave_check.isChecked(), None, "manual")
            return texture_bank.decode_item(self._rom, replace(item, usage=usage))
        return texture_bank.decode_item(self._rom, item, guess=item.kind == "unused" and self.guess_check.isChecked())

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
            label = f"{item.index:04X}  {item.name}{marker}"
            entry = QListWidgetItem(f"{item.index:04X}" if self.grid_check.isChecked() else label)
            entry.setToolTip(label)
            if self.grid_check.isChecked():
                entry.setSizeHint(QSize(88, 92))
            entry.setData(Qt.ItemDataRole.UserRole, item.index)
            self.list_widget.addItem(entry)
            if item.index == keep:
                selected = row
        if selected >= 0:
            self.list_widget.setCurrentRow(selected)
        self.list_widget.setUpdatesEnabled(True)
        self.list_widget.blockSignals(False)
        self.count_label.setText(f"{len(rows):,} of {len(self._items):,} textures")
        self._item_lookup = {item.index: item for item in self._items}
        self._thumbnail_row = 0
        self._thumbnail_timer.start()

    def _select_item(self, entry: QListWidgetItem | None, _previous=None) -> None:
        if entry is None:
            return
        index = entry.data(Qt.ItemDataRole.UserRole)
        item = next((candidate for candidate in self._items if candidate.index == index), None)
        if item is not None:
            self._set_sequences(item)
            self.show_item(item)

    # ------------------------------------------------------------------ view
    def show_item(self, item) -> None:
        if self._current is None or (item.table, item.index) != (self._current.table, self._current.index):
            self._preview_zoom = 1.0
        self._current = item
        self._set_info(item)
        self.usage_list.clear()
        for reference in item.references:
            row = QListWidgetItem(reference if reference.startswith("HUD ") else f"Open {reference}")
            row.setData(Qt.ItemDataRole.UserRole, reference)
            self.usage_list.addItem(row)
        guess = item.kind == "unused" and self.guess_check.isChecked()
        decoded = self._decode_current(item)
        if decoded is None:
            self._current_rgba = None
            self.image_label.setPixmap(QPixmap())
            self.image_label.setMinimumSize(0, 0)
            reason = ("no model or map loads it, so its format is unknown"
                      if item.kind == "unused" else "this format/size could not be decoded")
            self.image_label.setText(f"{item.name}: {reason}.")
            self.export_button.setEnabled(False)
            return
        width, height, rgba = decoded
        if self.manual_check.isChecked():
            self.format_value.setText(self.format_combo.currentText() + " (manual settings; unverified)")
        self._current_rgba = decoded
        self._render_preview()
        self.export_button.setEnabled(True)
        self.status_message.emit(f"Textures / {item.index:04X} — {item.name}")

    def _grid_changed(self, enabled):
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode if enabled else QListWidget.ViewMode.ListMode)
        self.list_widget.setGridSize(QSize(92, 96) if enabled else QSize())
        self._refresh_list()

    def _icon(self, item):
        key = item.table, item.index
        if key not in self._icons:
            decoded = texture_bank.decode_item(self._rom, item)
            if decoded:
                w, h, rgba = decoded
                pix = QPixmap.fromImage(QImage(rgba, w, h, w*4, QImage.Format.Format_RGBA8888).copy())
                self._icons[key] = QIcon(pix.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio))
            else:
                self._icons[key] = QIcon()
        return self._icons[key]

    def _load_thumbnails(self):
        # Short event-loop slices keep scrolling and bank changes responsive.
        for _ in range(12):
            if self._thumbnail_row >= self.list_widget.count():
                self._thumbnail_timer.stop()
                return
            row = self.list_widget.item(self._thumbnail_row)
            item = self._item_lookup.get(row.data(Qt.ItemDataRole.UserRole))
            if item:
                row.setIcon(self._icon(item))
            self._thumbnail_row += 1

    def _open_usage(self, item):
        user = item.data(Qt.ItemDataRole.UserRole)
        if user.startswith("HUD "):
            return
        kind, index = user.split()
        self.open_usage.emit(kind, int(index))

    def _set_sequences(self, item):
        self._sequence_timer.stop()
        self.sequence_play.setText("Play texture sequence")
        self.sequence_combo.blockSignals(True)
        self.sequence_combo.clear()
        for frames, label, ticks in self._sequences.get(item.index, ()):
            self.sequence_combo.addItem(label, (frames, ticks))
        self.sequence_combo.blockSignals(False)
        self._sequence_changed()

    def _sequence_changed(self):
        self._sequence_timer.stop()
        self.sequence_play.setText("Play texture sequence")
        self.sequence_frames.blockSignals(True)
        self.sequence_frames.clear()
        data = self.sequence_combo.currentData()
        self._sequence_frames = data[0] if data else ()
        for ordinal, index in enumerate(self._sequence_frames):
            item = self._item_lookup.get(index)
            row = QListWidgetItem(f"{ordinal}: {index:04X}")
            if item:
                row.setIcon(self._icon(item))
            self.sequence_frames.addItem(row)
        self.sequence_frames.blockSignals(False)
        for widget in (self.sequence_combo, self.sequence_frames, self.sequence_play):
            widget.setVisible(bool(data))
        if data:
            self._sequence_timer.setInterval(max(16, round(data[1]*1000/30)))

    def _sequence_frame(self, row):
        if 0 <= row < len(self._sequence_frames):
            item = self._item_lookup.get(self._sequence_frames[row])
            if item:
                self.show_item(item)

    def _sequence_next(self):
        if self._sequence_frames:
            self.sequence_frames.setCurrentRow((self.sequence_frames.currentRow()+1) % len(self._sequence_frames))

    def _sequence_toggle(self):
        if self._sequence_timer.isActive():
            self._sequence_timer.stop()
            self.sequence_play.setText("Play texture sequence")
        elif self._sequence_frames:
            self._sequence_timer.start()
            self.sequence_play.setText("Pause texture sequence")

    def hideEvent(self, event):
        self._sequence_timer.stop()
        self.sequence_play.setText("Play texture sequence")
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if (watched is self.image_scroll.viewport() and event.type() == QEvent.Type.Wheel
                and self._current_rgba is not None):
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self._preview_zoom = max(1 / 16, min(8.0,
                    self._preview_zoom * 1.25 ** max(-4, min(4, delta / 120))))
                self._render_preview()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _render_preview(self):
        """Scale the current decoded pixels without decoding again or changing exports."""
        if self._current_rgba is None:
            return
        width, height, rgba = self._current_rgba
        if self.trilinear_check.isChecked():
            base = PREVIEW_SIZE
        else:
            base = max(width, height) * max(1, min(16, PREVIEW_SIZE // max(width, height)))
        target = max(1, min(2048, round(base * self._preview_zoom)))
        scale = target / max(width, height)
        out_w, out_h = max(1, round(width * scale)), max(1, round(height * scale))
        if self.trilinear_check.isChecked():
            from .core.texture_filter import resample
            pixels = resample(rgba, width, height, out_w, out_h, "trilinear")
            image = QImage(pixels, out_w, out_h, out_w * 4, QImage.Format.Format_RGBA8888).copy()
            pixmap = QPixmap.fromImage(image)
        else:
            image = QImage(rgba, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
            pixmap = QPixmap.fromImage(image).scaled(out_w, out_h,
                                                     Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.FastTransformation)
        self.image_label.setText("")
        self.image_label.setPixmap(pixmap)
        self.image_label.setMinimumSize(pixmap.size())

    def _set_info(self, item) -> None:
        self.name_value.setText(item.name)
        self.id_value.setText(f"table {item.table}, entry {item.index} (0x{item.index:04X})")
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

    def export_jobs(self, items=None):
        """(label, job) pairs writing each shown, decodable texture as a PNG."""
        if items is None:
            items = self.shown()
        jobs = []
        for item in items:
            def job(folder: Path, item=item):
                decoded = texture_bank.decode_item(self._rom, item)
                if decoded is None:
                    raise ValueError("not decodable")
                width, height, rgba = decoded
                (folder / (safe_file_stem(item) + ".png")).write_bytes(texture_bank.rgba_png(width, height, rgba))
            jobs.append((item.name, job))
        return jobs

    def export_all(self, folder: Path | None = None):
        from .batch_export import export_all
        self.ensure_loaded()
        result = export_all(self, "Export textures", self.export_jobs(), folder)
        if result is not None:
            self.status_message.emit(result.summary() + (f" ({len(result.failed)} not decodable)" if result.failed else ""))
        return result

    def _export_shown(self) -> None:
        self.export_all()
