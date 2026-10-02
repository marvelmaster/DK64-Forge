"""PySide6 JFG Forge window/workflow adapted to the verified DK64 backend.

Layout and interaction follow MIT-licensed jfg_forge.main_window/app, Copyright
2026 Marvelmaster: tabbed main window, timing modes with a Movement / Speed
slider, joint inspector, reference marking and the three export entries.
JFG asset parsing and its runtime timing tables are not used (DK64 has its own).
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from pathlib import Path
import sys
from typing import Callable

import numpy as np

from PySide6.QtCore import QElapsedTimer, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QSurfaceFormat
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSlider,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

# Timing modes (JFG Forge "Game Timing" / "Technical"). DK64 has one observed rate:
# Entry 4 advances one adjusted unit per main-loop tick at 30 Hz (ENTRY4_PLAYBACK_TIMING.md);
# for every other clip that rate is a DIAGNOSTIC ASSUMPTION.
TIMING_GAME = "game"
TIMING_TECHNICAL = "technical"
GAME_UNITS_PER_SECOND = 30.0
MIN_SPEED_TICK, MAX_SPEED_TICK = 1, 50  # Movement / Speed 0.1 .. 5.0

from .characters import CHARACTERS, VARIANT_NORMAL, variants_for
from .debug_view import DEFAULT_VIEW_MODE, ViewMode
from .export import ExportKind, export_gltf
from .preview_data import PreviewScene
from .audio_tab import AudioTab
from .model_browser import KIND_ACTOR, KIND_MAP, KIND_PROP, ModelBrowserTab
from .texture_tab import TextureTab
from .session import (CHARACTER, RomParseError, RomReadError,
                      RomSession, UnsupportedRomError, load_rom)
from .viewport import ModelViewport


def _select_rom(parent: QWidget) -> Path | None:
    filename, _filter = QFileDialog.getOpenFileName(
        parent, "Load Donkey Kong 64 ROM", "",
        "Nintendo 64 ROMs (*.n64 *.z64 *.v64);;All files (*)",
    )
    return Path(filename) if filename else None


class RomWelcomeWindow(QMainWindow):
    """Small pre-ROM window following JFG Forge's welcome layout."""

    def __init__(self, on_rom_selected: Callable[[Path], None]) -> None:
        super().__init__()
        self._on_rom_selected = on_rom_selected
        self.setWindowTitle("DK64 Forge")
        self.resize(720, 360)
        file_menu = self.menuBar().addMenu("&File")
        self.load_rom_action = QAction("Load ROM...", self)
        self.load_rom_action.triggered.connect(self._choose_rom)
        file_menu.addAction(self.load_rom_action)
        message = QLabel(
            "Welcome to DK64 Forge\n\n"
            "Load your own supported Donkey Kong 64 US revision 0 ROM to open "
            "the character viewer.\nThe ROM is read locally and is not copied by Forge."
        )
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setWordWrap(True)
        self.setCentralWidget(message)
        self.rom_status_label = QLabel("ROM: No ROM loaded")
        self.statusBar().addPermanentWidget(self.rom_status_label)

    def _choose_rom(self, _checked: bool = False) -> None:
        path = _select_rom(self)
        if path is not None:
            self._on_rom_selected(path)


class MainWindow(QMainWindow):
    """JFG-like left asset browser and right OpenGL viewport per supported character."""

    def __init__(self, source, preview: PreviewScene,
                 on_rom_selected: Callable[[Path], None]) -> None:
        super().__init__()
        self.source = source
        self.preview = preview
        # Keyed by (character key, model variant, Table-11 id); starts with the default clip.
        self._preview_cache: OrderedDict[tuple[str, str, int], PreviewScene] = OrderedDict(
            (((source.character.key, source.character.variant, source.character.default_animation), preview),))
        self._selected_animation_id: int | None = None
        self._on_rom_selected = on_rom_selected
        self._playing = False
        self._time_seconds = 0.0
        self._current_frame = 0
        self._timing_mode = TIMING_GAME
        self._speed = 1.0
        self._reference: tuple[str, int] | None = None
        self._last_skeleton = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance_playback)
        self._elapsed = QElapsedTimer()
        self.setWindowTitle("DK64 Forge")
        self.resize(1180, 760)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_information_panel())
        _positions, skeleton = self.preview.bind_pose()
        self.viewport = ModelViewport(preview.render_data, skeleton)
        # G_TEXTURE_GEN UVs follow the viewer camera, as the RSP does in game.
        self.viewport.set_uv_provider(
            lambda eye, target: self.preview.texgen_uvs(eye, target))
        self.viewport.initialization_failed.connect(self._show_renderer_error)
        splitter.addWidget(self.viewport)
        splitter.setSizes([260, 920])
        splitter.setStretchFactor(1, 1)
        # Tabbed layout as in JFG Forge; further tabs (Textures, Models, Levels, Audio)
        # are added by their own modules.
        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "Characters")
        self.models_tab = ModelBrowserTab(source.normalized, (KIND_ACTOR, KIND_PROP), "Models (actors and props)")
        self.levels_tab = ModelBrowserTab(source.normalized, (KIND_MAP,), "Levels (map geometry)")
        for tab, label in ((self.models_tab, "Models"), (self.levels_tab, "Levels")):
            tab.status_message.connect(lambda text: self.statusBar().showMessage(text, 10000))
            self.tabs.addTab(tab, label)
        self.audio_tab = AudioTab(source.normalized)
        self.tabs.addTab(self.audio_tab, "Audio")
        self.texture_tab = TextureTab(source.normalized)
        self.texture_tab.status_message.connect(lambda text: self.statusBar().showMessage(text, 10000))
        self.tabs.addTab(self.texture_tab, "Textures")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self._build_export_menu()
        self.rom_status_label = QLabel(f"ROM: {source.path.name}")
        self.rom_status_label.setToolTip(str(source.path))
        self.statusBar().addPermanentWidget(self.rom_status_label)
        self.previous_shortcut = QShortcut(QKeySequence("Ctrl+Left"), self)
        self.previous_shortcut.activated.connect(lambda: self._navigate_animation(-1))
        self.next_shortcut = QShortcut(QKeySequence("Ctrl+Right"), self)
        self.next_shortcut.activated.connect(lambda: self._navigate_animation(1))
        self._select_animation()

    def _build_export_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.load_rom_action = QAction("Load ROM...", self)
        self.load_rom_action.triggered.connect(self._choose_rom)
        file_menu.addAction(self.load_rom_action)
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("Export")
        self.export_current_action = QAction("Export Current Selection...", self)
        self.export_current_action.triggered.connect(self._export_current)
        export_menu.addAction(self.export_current_action)
        self.export_model_action = QAction("Export Model...", self)
        self.export_model_action.triggered.connect(
            lambda _checked=False: self._export(ExportKind.STATIC_TEXTURED))
        export_menu.addAction(self.export_model_action)
        self.export_animation_only_action = QAction("Export Current Animation...", self)
        self.export_animation_only_action.triggered.connect(
            lambda _checked=False: self._export(ExportKind.ANIMATION_ONLY))
        export_menu.addAction(self.export_animation_only_action)
        self.export_animation_action = QAction("Export Model + Current Animation...", self)
        self.export_animation_action.triggered.connect(
            lambda _checked=False: self._export(ExportKind.ANIMATED))
        export_menu.addAction(self.export_animation_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def _build_information_panel(self) -> QWidget:
        panel = QFrame()
        panel.setMinimumWidth(220)
        layout = QVBoxLayout(panel)
        heading = QLabel("Asset / Model Info")
        heading.setStyleSheet("font-weight: bold; font-size: 15px;")
        layout.addWidget(heading)
        self.character_combo = QComboBox()
        for spec in CHARACTERS.values():
            self.character_combo.addItem(spec.name, spec.model_id)
        self.character_combo.setCurrentIndex(
            list(CHARACTERS).index(self.source.character.key))
        layout.addWidget(self.character_combo)
        self.variant_combo = QComboBox()
        self.variant_combo.setToolTip("Weapon drawn: the hand-state bits the game sets when the Kong pulls "
                                      "out its weapon. With instrument: the Kong's instrument model.")
        self._fill_variant_combo()
        layout.addWidget(self.variant_combo)
        form = QFormLayout()
        self.model_name_value = QLabel()
        self.model_prop_value = QLabel()
        self.model_prop_value.setWordWrap(True)
        self.model_source_vertices_value = QLabel()
        self.model_render_vertices_value = QLabel()
        self.model_faces_value = QLabel()
        self.model_groups_value = QLabel()
        self.model_joints_value = QLabel()
        self.model_textures_value = QLabel()
        for label, widget in (
            ("Name", self.model_name_value), ("Model", self.model_prop_value),
            ("Source vertices", self.model_source_vertices_value),
            ("Render vertices", self.model_render_vertices_value),
            ("Faces", self.model_faces_value), ("Groups", self.model_groups_value),
            ("Joints", self.model_joints_value), ("Textures", self.model_textures_value),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)

        animation_heading = QLabel("Animation Browser")
        animation_heading.setStyleSheet("font-weight: bold; font-size: 15px; margin-top: 12px;")
        layout.addWidget(animation_heading)
        self.animation_combo = QComboBox()
        self.animation_combo.setMinimumContentsLength(24)
        self.animation_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._fill_animation_combo(dk_only=False, keep=self.source.character.default_animation)
        self.dk_only_check = QCheckBox()
        self.dk_only_check.toggled.connect(self._filter_animations)
        layout.addWidget(self.dk_only_check)
        layout.addWidget(self.animation_combo)
        navigation = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        self.previous_button.setToolTip("Previous selection (Ctrl+Left)")
        self.next_button.setToolTip("Next selection (Ctrl+Right)")
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.next_button)
        layout.addLayout(navigation)
        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.stop_button = QPushButton("Stop")
        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)
        self.playback_state_label = QLabel("Stopped")
        layout.addWidget(self.playback_state_label)
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 97)
        self.time_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.time_slider.setTickInterval(10)
        layout.addWidget(self.time_slider)
        self.time_label = QLabel()
        self.sample_label = QLabel()
        layout.addWidget(self.time_label)
        layout.addWidget(self.sample_label)
        timing_form = QFormLayout()
        self.timing_mode_combo = QComboBox()
        self.timing_mode_combo.addItem("Game rate (30 units/s)", TIMING_GAME)
        self.timing_mode_combo.addItem("Technical (1 sample/s)", TIMING_TECHNICAL)
        self.timing_mode_combo.setToolTip(
            "Game rate: one adjusted unit per 30 Hz main-loop tick (observed for DK Entry 4; "
            "a diagnostic assumption for other clips). Technical: one sample per second.")
        speed_row = QHBoxLayout()
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(MIN_SPEED_TICK, MAX_SPEED_TICK)
        self.speed_slider.setValue(10)
        self.speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.speed_slider.setTickInterval(10)
        self.speed_slider.setToolTip("Playback speed multiplier (preview only; exports keep the clip's times).")
        self.speed_value = QLabel("1.0x")
        speed_row.addWidget(self.speed_slider)
        speed_row.addWidget(self.speed_value)
        self.timing_status_label = QLabel()
        self.timing_status_label.setWordWrap(True)
        timing_form.addRow("Timing", self.timing_mode_combo)
        timing_form.addRow("Speed", speed_row)
        layout.addLayout(timing_form)
        layout.addWidget(self.timing_status_label)
        reference_row = QHBoxLayout()
        self.mark_reference_button = QPushButton("Mark Current as Reference")
        self.goto_reference_button = QPushButton("Go to Reference")
        self.goto_reference_button.setEnabled(False)
        reference_row.addWidget(self.mark_reference_button)
        reference_row.addWidget(self.goto_reference_button)
        layout.addLayout(reference_row)
        self.reference_label = QLabel("Reference: none")
        self.reference_label.setWordWrap(True)
        layout.addWidget(self.reference_label)
        technical = QFormLayout()
        self.animation_index_value = QLabel("table 11 / entry 4")
        self.animation_samples_value = QLabel("98 interior poses")
        self.animation_domain_value = QLabel("adjusted time 0..97; endpoint unknown")
        self.animation_timing_value = QLabel("adjusted time / 30.0 seconds (diagnostic)")
        self.animation_context_value = QLabel(
            "Experimental idle-like; prefix translation T(u); no Actor/world placement; "
            "adjustments omitted; runtime_faithful=false"
        )
        self.animation_context_value.setWordWrap(True)
        self.animation_ownership_value = QLabel()
        self.animation_semantic_value = QLabel()
        self.animation_prefix_value = QLabel()
        self.animation_root_value = QLabel()
        self.animation_root_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.animation_loop_value = QLabel(
            "Forge wraps the safe range for browsing; game endpoint/loop behaviour UNKNOWN")
        self.animation_loop_value.setWordWrap(True)
        for label, widget in (
            ("Index", self.animation_index_value), ("Samples", self.animation_samples_value),
            ("Domain", self.animation_domain_value), ("Timing", self.animation_timing_value),
            ("Ownership", self.animation_ownership_value),
            ("Semantic", self.animation_semantic_value),
            ("Prefix", self.animation_prefix_value),
            ("Root (bone 0)", self.animation_root_value),
            ("Loop", self.animation_loop_value),
            ("Context", self.animation_context_value),
        ):
            technical.addRow(label, widget)
        layout.addLayout(technical)

        debug_heading = QLabel("Viewport Debug")
        debug_heading.setStyleSheet("font-weight: bold; font-size: 15px; margin-top: 12px;")
        layout.addWidget(debug_heading)
        debug_form = QFormLayout()
        self.view_mode_combo = QComboBox()
        for mode in ViewMode:
            self.view_mode_combo.addItem(mode.value, mode.value)
        self.view_mode_combo.setCurrentIndex(list(ViewMode).index(DEFAULT_VIEW_MODE))
        self.joint_combo = QComboBox()
        self.joint_value = QLabel("bone_00")
        # Joint inspector as in JFG Forge: id, parent, current position, rest offset, geometry.
        self.joint_parent_value = QLabel()
        self.joint_position_value = QLabel()
        self.joint_rest_value = QLabel()
        self.joint_geometry_value = QLabel()
        for widget in (self.joint_position_value, self.joint_rest_value):
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        debug_form.addRow("View", self.view_mode_combo)
        debug_form.addRow("Joint", self.joint_combo)
        debug_form.addRow("Selected", self.joint_value)
        debug_form.addRow("Parent", self.joint_parent_value)
        debug_form.addRow("Position XYZ", self.joint_position_value)
        debug_form.addRow("Rest local XYZ", self.joint_rest_value)
        debug_form.addRow("Geometry", self.joint_geometry_value)
        layout.addLayout(debug_form)
        self.reset_view_button = QPushButton("Reset View")
        layout.addWidget(self.reset_view_button)
        self.limitations_label = QLabel()
        self.limitations_label.setWordWrap(True)
        layout.addWidget(self.limitations_label)
        self._apply_character_info()
        layout.addStretch(1)

        self.animation_combo.currentIndexChanged.connect(self._select_animation)
        self.character_combo.currentIndexChanged.connect(self._select_character)
        self.variant_combo.currentIndexChanged.connect(self._select_variant)
        self.previous_button.clicked.connect(lambda _checked=False: self._navigate_animation(-1))
        self.next_button.clicked.connect(lambda _checked=False: self._navigate_animation(1))
        self.play_button.clicked.connect(self._toggle_playback)
        self.stop_button.clicked.connect(self._stop_playback)
        self.time_slider.valueChanged.connect(self._scrub)
        self.timing_mode_combo.currentIndexChanged.connect(self._set_timing_mode)
        self.speed_slider.valueChanged.connect(self._set_speed)
        self.mark_reference_button.clicked.connect(self._mark_reference)
        self.goto_reference_button.clicked.connect(self._goto_reference)
        self.view_mode_combo.currentIndexChanged.connect(self._select_view_mode)
        self.joint_combo.currentIndexChanged.connect(self._select_joint)
        self.reset_view_button.clicked.connect(lambda _checked=False: self.viewport.reset_view())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(460)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(panel)
        return scroll

    def _choose_rom(self, _checked: bool = False) -> None:
        path = _select_rom(self)
        if path is not None:
            self._on_rom_selected(path)

    def _apply_character_info(self) -> None:
        """Model info, joint list and filter text for the active character."""
        spec = self.source.character
        self.model_name_value.setText(spec.display_name)
        self.model_prop_value.setText(f"Model {spec.model_id} · table 5 / {spec.table5_entry}")
        self.model_source_vertices_value.setText(str(spec.vertices))
        self.model_render_vertices_value.setText(str(len(self.preview.render_data.positions)))
        self.model_faces_value.setText(str(spec.triangles))
        self.model_groups_value.setText(str(len(self.preview.render_data.batches)))
        self.model_joints_value.setText(str(spec.bones))
        self.model_textures_value.setText(
            f"{len(self.preview.render_data.textures)} textures "
            f"({spec.stored_uv_triangles} stored-UV / {spec.texgen_triangles} texgen faces)")
        short = "DK" if spec.key == "dk" else spec.name.split()[0]
        self.dk_only_check.setText(f"Only {short} clips (Table-13 animation code)")
        self.dk_only_check.setToolTip(
            f"Hide structurally compatible clips that {spec.name}'s animation code never plays.")
        with QSignalBlocker(self.joint_combo):
            self.joint_combo.clear()
            for joint in range(spec.bones):
                self.joint_combo.addItem(f"bone_{joint:02d}", joint)
        self.joint_value.setText("bone_00")
        self.limitations_label.setText(
            f"Texture preview: {spec.texgen_triangles} G_TEXTURE_GEN faces are regenerated live "
            "from the viewer camera (spherical texgen + hilite tile origin); exports bake them for "
            "a fixed front camera. No RDP combiner/lighting; dynamic slots use first-frame fallbacks.")

    def _fill_variant_combo(self) -> None:
        with QSignalBlocker(self.variant_combo):
            self.variant_combo.clear()
            for variant, label in variants_for(CHARACTERS[self.source.character.key]):
                self.variant_combo.addItem(label, variant)
            self.variant_combo.setCurrentIndex(max(self.variant_combo.findData(self.source.character.variant), 0))

    def _select_variant(self, _index: int = -1) -> None:
        self._select_character(variant=str(self.variant_combo.currentData()))

    def _select_character(self, _index: int = -1, variant: str = VARIANT_NORMAL) -> None:
        key = list(CHARACTERS)[self.character_combo.currentIndex()]
        if (key, variant) == (self.source.character.key, self.source.character.variant):
            return
        self._pause()
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            source = self.source.for_character(key, variant)
            cache_key = (key, variant, source.character.default_animation)
            scene = self._preview_cache.get(cache_key) or PreviewScene.from_rom(source)
        except Exception as exc:
            QMessageBox.critical(self, "DK64 Forge character error", str(exc))
            with QSignalBlocker(self.character_combo):
                self.character_combo.setCurrentIndex(list(CHARACTERS).index(self.source.character.key))
            self._fill_variant_combo()
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.source = source
        self.preview = scene
        self._preview_cache[cache_key] = scene
        self._fill_variant_combo()
        _positions, skeleton = scene.bind_pose()
        self.viewport.set_model_data(scene.render_data, skeleton)
        self._apply_character_info()
        with QSignalBlocker(self.animation_combo):
            self._fill_animation_combo(self.dk_only_check.isChecked(),
                                       source.character.default_animation)
        self._select_animation()
        self.statusBar().showMessage(f"{source.character.display_name}: {len(source.animations)} clips", 8000)

    def _is_reference(self, animation_id) -> bool:
        """Entry 4 is only special for DK (bit-exact root and observed timing)."""
        return animation_id == 4 and self.source.character.key == "dk"

    def _tab_changed(self, index: int) -> None:
        """Tabs load their data the first time they are opened (as in JFG Forge)."""
        widget = self.tabs.widget(index)
        if hasattr(widget, "ensure_loaded"):
            widget.ensure_loaded()
        if index != 0:
            self._pause()
        if widget is not self.audio_tab:
            self.audio_tab.stop()

    # --- timing, reference and joint inspector (JFG Forge parity) -------------------------

    def _samples_per_second(self) -> float:
        base = GAME_UNITS_PER_SECOND if self._timing_mode == TIMING_GAME else 1.0
        return base * self._speed

    def _set_timing_mode(self, _index: int = -1) -> None:
        current_sample = self._current_frame
        self._timing_mode = self.timing_mode_combo.currentData()
        self._time_seconds = (current_sample - self.preview.safe_first) / self._samples_per_second()
        self._update_timing_status()
        self._show_frame(current_sample)

    def _set_speed(self, tick: int) -> None:
        current_sample = self._current_frame
        self._speed = tick / 10.0
        self.speed_value.setText(f"{self._speed:.1f}x")
        self._time_seconds = (current_sample - self.preview.safe_first) / self._samples_per_second()
        self._update_timing_status()

    def _update_timing_status(self) -> None:
        animation_id = self.animation_combo.currentData()
        rate = self._samples_per_second()
        if animation_id is None:
            self.timing_status_label.setText("Static pose: no timing.")
        elif self._timing_mode == TIMING_TECHNICAL:
            self.timing_status_label.setText(
                f"Technical timing: {rate:.3g} samples/s (1 sample/s x {self._speed:.1f}).")
        elif self._is_reference(animation_id):
            self.timing_status_label.setText(
                f"RUNTIME OBSERVED rate for DK Entry 4: 30 units/s; playing {rate:.3g} units/s "
                f"at speed {self._speed:.1f}.")
        else:
            self.timing_status_label.setText(
                f"DIAGNOSTIC ASSUMPTION: 30 units/s (Entry-4 rate reused); playing {rate:.3g} "
                f"units/s at speed {self._speed:.1f}.")

    def _mark_reference(self, _checked: bool = False) -> None:
        animation_id = self.animation_combo.currentData()
        if animation_id is None:
            self.statusBar().showMessage("Select an animation to mark it as reference.", 5000)
            return
        self._reference = (self.source.character.key, animation_id)
        self.reference_label.setText(
            f"Reference: {self.source.character.name} · {self.animation_combo.currentText()}")
        self.goto_reference_button.setEnabled(True)

    def _goto_reference(self, _checked: bool = False) -> None:
        if self._reference is None:
            return
        key, animation_id = self._reference
        if key != self.source.character.key:
            self.character_combo.setCurrentIndex(list(CHARACTERS).index(key))
        index = self.animation_combo.findData(animation_id)
        if index < 0 and self.dk_only_check.isChecked():
            self.dk_only_check.setChecked(False)
            index = self.animation_combo.findData(animation_id)
        if index >= 0:
            self.animation_combo.setCurrentIndex(index)

    def _update_joint_details(self) -> None:
        joint = self.joint_combo.currentData()
        if joint is None:
            return
        parents = self.preview.parent_ordinals
        parent = parents[joint] if joint < len(parents) else None
        self.joint_parent_value.setText("none (root)" if parent is None else f"bone_{parent:02d}")
        skeleton = self._last_skeleton
        if skeleton is not None and joint < len(skeleton.joint_positions):
            x, y, z = skeleton.joint_positions[joint]
            self.joint_position_value.setText(f"{x:.3f}, {y:.3f}, {z:.3f}")
        bind = self.preview.bind_globals
        local = bind[joint] if parent is None else np.linalg.inv(bind[parent]) @ bind[joint]
        lx, ly, lz = (float(v) for v in local[:3, 3])
        self.joint_rest_value.setText(f"{lx:.3f}, {ly:.3f}, {lz:.3f}")
        corners = int(np.count_nonzero(self.preview.rigid_joints == joint))
        self.joint_geometry_value.setText(
            f"{corners // 3} faces ({corners} render corners)" if corners else "no geometry (pure joint)")

    def _update_root_motion(self) -> None:
        skeleton = self._last_skeleton
        if skeleton is None or self.animation_combo.currentData() is None:
            self.animation_root_value.setText("not applicable")
            return
        x, y, z = skeleton.joint_positions[0]
        self.animation_root_value.setText(
            f"{x:.3f}, {y:.3f}, {z:.3f} (animation prefix translation; origin-centered, "
            "gameplay meaning UNKNOWN)")

    def _export_current(self, _checked: bool = False) -> None:
        kind = (ExportKind.ANIMATED if self.animation_combo.currentData() is not None
                else ExportKind.STATIC_TEXTURED)
        self._export(kind)

    def _export(self, kind: ExportKind) -> None:
        animation_id = self.animation_combo.currentData()
        if kind in (ExportKind.ANIMATED, ExportKind.ANIMATION_ONLY) and animation_id is None:
            QMessageBox.information(self, "DK64 Forge export", "Select an animation to export.")
            return
        spec = self.source.character
        default = (f"{spec.key}_anim_{animation_id:04X}.gltf" if kind is ExportKind.ANIMATED
                   else f"{spec.key}_anim_{animation_id:04X}_animation_only.gltf"
                   if kind is ExportKind.ANIMATION_ONLY
                   else f"{spec.key}_static_textured.gltf")
        filename, _filter = QFileDialog.getSaveFileName(
            self, f"Export {spec.name} glTF", default, "glTF 2.0 (*.gltf)")
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".gltf":
            path = path.with_suffix(".gltf")
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            result = (export_gltf(self.source, path, kind)
                      if kind is ExportKind.STATIC_TEXTURED or (
                          kind is ExportKind.ANIMATED and self._is_reference(animation_id))
                      else export_gltf(self.source, path, kind, animation_id=animation_id))
        except Exception as exc:
            QMessageBox.critical(self, "DK64 Forge export error", str(exc))
            self.statusBar().showMessage(f"Export failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Exported {result.path}", 15000)

    def _fill_animation_combo(self, dk_only: bool, keep) -> None:
        self.animation_combo.clear()
        for descriptor in self.source.animations:
            if dk_only and not descriptor.dk_owned:
                continue
            self.animation_combo.addItem(descriptor.label, descriptor.table11_id)
        self.animation_combo.addItem("Static pose / model", None)
        index = next((i for i in range(self.animation_combo.count())
                      if self.animation_combo.itemData(i) == keep), None)
        self.animation_combo.setCurrentIndex(index if index is not None else 0)

    def _filter_animations(self, dk_only: bool) -> None:
        current = self.animation_combo.currentData()
        with QSignalBlocker(self.animation_combo):
            self._fill_animation_combo(dk_only, current)
        if self.animation_combo.currentData() != current:
            self._select_animation(self.animation_combo.currentIndex())

    def _select_animation(self, _index: int = -1) -> None:
        self._pause()
        animation_id = self.animation_combo.currentData()
        animated = animation_id is not None
        cache_key = (self.source.character.key, self.source.character.variant, animation_id)
        if animated and cache_key not in self._preview_cache:
            try:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                scene = PreviewScene.from_animation(self.source, animation_id)
            except Exception as exc:
                QMessageBox.critical(self, "DK64 Forge animation preview error",
                                     f"Table-11 entry {animation_id:04X}: {exc}")
                self.statusBar().showMessage(f"Animation {animation_id:04X} preview failed: {exc}")
                with QSignalBlocker(self.animation_combo):
                    old_index = next(i for i in range(self.animation_combo.count())
                                     if self.animation_combo.itemData(i) == self._selected_animation_id)
                    self.animation_combo.setCurrentIndex(old_index)
                return
            finally:
                QApplication.restoreOverrideCursor()
            self._preview_cache[cache_key] = scene
            if len(self._preview_cache) > 3:
                self._preview_cache.popitem(last=False)
        if animated:
            self._preview_cache.move_to_end(cache_key)
            self.preview = self._preview_cache[cache_key]
            self._selected_animation_id = animation_id
        self.play_button.setEnabled(animated)
        self.stop_button.setEnabled(animated)
        self.time_slider.setEnabled(animated)
        self.export_animation_only_action.setEnabled(animated)
        self.export_animation_action.setEnabled(animated)
        if animated:
            descriptor = next(d for d in self.source.animations if d.table11_id == animation_id)
            self.animation_index_value.setText(f"table 11 / entry {animation_id}")
            self.animation_samples_value.setText(f"{descriptor.sample_count} interior poses")
            self.animation_domain_value.setText(
                f"adjusted time {descriptor.safe_first}..{descriptor.safe_last}; endpoint unknown")
            self.animation_timing_value.setText(
                "adjusted time / 30.0 s (Entry-4 observed unit-rate)" if self._is_reference(animation_id)
                else "adjusted time / 30.0 s (diagnostic; rate unverified)")
            name = self.source.character.name
            self.animation_ownership_value.setText(
                f"{name} (Table-13 animation code + runtime)" if descriptor.ownership.endswith("+RUNTIME")
                else f"{name} (Table-13 animation code)" if descriptor.ownership.endswith("STATIC_TABLE13")
                else "DK runtime observed" if descriptor.ownership == "DK_OWNERSHIP_VERIFIED_RUNTIME"
                else f"not in {name}'s animation code; structurally compatible")
            self.animation_semantic_value.setText(descriptor.semantic_evidence)
            self.animation_prefix_value.setText(
                "included; Entry-4 scalar verified" if self._is_reference(animation_id)
                else "included; scalar is a diagnostic assumption")
            self.animation_context_value.setText(
                "Origin-centered; Actor/world placement and adjustment rows omitted; "
                "runtime_faithful=false")
            with QSignalBlocker(self.time_slider):
                self.time_slider.setRange(descriptor.safe_first, descriptor.safe_last)
                self.time_slider.setValue(descriptor.safe_first)
            self._time_seconds = 0.0
            self._current_frame = descriptor.safe_first
        else:
            self._selected_animation_id = None
            self.animation_index_value.setText("static bind pose")
            self.animation_samples_value.setText("one static pose")
            self.animation_domain_value.setText("not applicable")
            self.animation_timing_value.setText("not applicable")
            self.animation_ownership_value.setText(f"canonical {self.source.character.name} model")
            self.animation_semantic_value.setText("not applicable")
            self.animation_prefix_value.setText("not applicable")
        if animated:
            self._show_frame(self.preview.safe_first)
            self._start_playback()
        else:
            self.playback_state_label.setText("Static")
            positions, skeleton = self.preview.bind_pose()
            self.viewport.set_scene_data(positions, skeleton)
            self._last_skeleton = skeleton
            self.time_label.setText("Static canonical model")
            self.sample_label.setText(f"Hand-state mask {self.source.character.hand_mask}")
            self._update_joint_details()
            self._update_root_motion()
        self._update_timing_status()

    def _navigate_animation(self, offset: int) -> None:
        self.animation_combo.setCurrentIndex(
            (self.animation_combo.currentIndex() + offset) % self.animation_combo.count())

    def _select_view_mode(self, _index: int = -1) -> None:
        self.viewport.set_view_mode(ViewMode(self.view_mode_combo.currentData()))

    def _select_joint(self, _index: int = -1) -> None:
        joint = self.joint_combo.currentData()
        if joint is None:
            return
        self.viewport.set_selected_joint(joint)
        self.joint_value.setText(f"bone_{joint:02d}")
        self._update_joint_details()

    def _show_frame(self, frame: int) -> None:
        first, last = self.preview.safe_first, self.preview.safe_last
        self._current_frame = max(first, min(last, frame))
        positions, skeleton = self.preview.pose(self._current_frame)
        self.viewport.set_scene_data(positions, skeleton)
        self._last_skeleton = skeleton
        with QSignalBlocker(self.time_slider):
            self.time_slider.setValue(self._current_frame)
        rate = self._samples_per_second()
        self.time_label.setText(
            f"Time: {(self._current_frame - first) / rate:.3f} s / {(last - first) / rate:.3f} s")
        self.sample_label.setText(f"Adjusted sample: {self._current_frame} / {last}")
        self._update_joint_details()
        self._update_root_motion()

    def _toggle_playback(self, _checked: bool = False) -> None:
        if self.animation_combo.currentData() is None:
            return
        if self._playing:
            self._pause()
            return
        self._start_playback()

    def _start_playback(self) -> None:
        if self.animation_combo.currentData() is None:
            return
        self._playing = True
        self.play_button.setText("Pause")
        self.playback_state_label.setText("Playing")
        self._elapsed.start()
        self._timer.start()

    def _pause(self) -> None:
        was_playing = self._playing
        self._playing = False
        self._timer.stop()
        self.play_button.setText("Play")
        if was_playing:
            self.playback_state_label.setText("Paused")

    def _stop_playback(self, _checked: bool = False) -> None:
        self._pause()
        self._time_seconds = 0.0
        self._show_frame(self.preview.safe_first)
        self.playback_state_label.setText("Stopped")

    def _scrub(self, frame: int) -> None:
        self._time_seconds = (frame - self.preview.safe_first) / self._samples_per_second()
        self._show_frame(frame)
        if self._playing:
            self._elapsed.restart()

    def _advance_playback(self) -> None:
        if not self._playing:
            return
        self._advance_playback_by(self._elapsed.restart() / 1000.0)

    def _advance_playback_by(self, elapsed_seconds: float) -> None:
        """Advance the viewport clock and wrap within this clip's safe range."""
        if not self._playing:
            return
        self._time_seconds = max(0.0, self._time_seconds + elapsed_seconds)
        count = self.preview.safe_last - self.preview.safe_first + 1
        relative_sample = int(self._time_seconds * self._samples_per_second() + 1e-7) % count
        frame = self.preview.safe_first + relative_sample
        if frame != self._current_frame:
            self._show_frame(frame)

    def _show_renderer_error(self, message: str) -> None:
        self.statusBar().showMessage(f"OpenGL renderer unavailable: {message}")
        QMessageBox.warning(self, "DK64 Forge renderer", message)


def _request_opengl_33() -> None:
    format_ = QSurfaceFormat()
    format_.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    format_.setVersion(3, 3)
    format_.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    format_.setDepthBufferSize(24)
    format_.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(format_)


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dk64_forge", description="DK64 Forge viewer and glTF exporter")
    parser.add_argument("--rom", type=Path, default=None,
                        help="Donkey Kong 64 US rev 0 ROM to open directly (otherwise File > Load ROM).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(sys.argv[1:] if argv is None else argv)
    _request_opengl_33()
    app = QApplication.instance() or QApplication([])
    session = RomSession()
    windows: list[QMainWindow] = []

    def show_loaded_rom(path: Path) -> None:
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            candidate = load_rom(path)
            preview = PreviewScene.from_rom(candidate)
        except (UnsupportedRomError, RomReadError, RomParseError, ValueError, OSError) as exc:
            QMessageBox.critical(windows[-1], "DK64 Forge ROM error", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(windows[-1], "DK64 Forge preview error", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        session.source = candidate
        new_window = MainWindow(candidate, preview, show_loaded_rom)
        old = windows[-1]
        windows.append(new_window)
        new_window.show()
        old.close()

    welcome = RomWelcomeWindow(show_loaded_rom)
    windows.append(welcome)
    welcome.show()
    if arguments.rom is not None:
        QTimer.singleShot(0, lambda: show_loaded_rom(arguments.rom))
    return app.exec()
