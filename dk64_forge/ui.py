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
    QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox,
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
        # Keyed by character, model variant, Table-11 id and procedural hair mode.
        self._preview_cache: OrderedDict[tuple[str, str, int, bool], PreviewScene] = OrderedDict(
            (((source.character.key, source.character.variant, source.character.default_animation, False), preview),))
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
        self.viewport.set_grid_visible(True)  # ground plane at the Kongs' feet (y = 0)
        # G_TEXTURE_GEN UVs follow the viewer camera, as the RSP does in game.
        self.viewport.set_uv_provider(
            lambda eye, target: self.preview.texgen_uvs(eye, target))
        self.viewport.initialization_failed.connect(self._show_renderer_error)
        self._update_attachment()
        splitter.addWidget(self.viewport)
        splitter.setSizes([260, 920])
        splitter.setStretchFactor(1, 1)
        self.tabs = QTabWidget()
        self.model_tabs = QTabWidget()
        self.model_tabs.addTab(splitter, "Characters")
        self.models_tab = ModelBrowserTab(source.normalized, (KIND_ACTOR, KIND_PROP), "Other models (actors and props)")
        self.levels_tab = ModelBrowserTab(source.normalized, (KIND_MAP,), "Levels (map geometry)")
        self.models_tab.status_message.connect(lambda text: self.statusBar().showMessage(text, 10000))
        self.levels_tab.status_message.connect(lambda text: self.statusBar().showMessage(text, 10000))
        self.model_tabs.addTab(self.models_tab, "Other models")
        self.model_tabs.currentChanged.connect(self._model_tab_changed)
        self.tabs.addTab(self.model_tabs, "Models")
        self.tabs.addTab(self.levels_tab, "Levels")
        self.audio_tab = AudioTab(source.normalized)
        self.tabs.addTab(self.audio_tab, "Audio")
        self.texture_tab = TextureTab(source.normalized)
        self.texture_tab.status_message.connect(lambda text: self.statusBar().showMessage(text, 10000))
        self.tabs.addTab(self.texture_tab, "Textures")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        from .dropdown_search import add_dropdown_search
        add_dropdown_search((self.animation_combo, self.models_tab.clip_combo, self.levels_tab.chunk_combo))
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
        self.export_all_animations_action = QAction("Export All Animations of Current Character...", self)
        self.export_all_animations_action.triggered.connect(lambda _checked=False: self.export_all_animations())
        export_menu.addSeparator()
        export_menu.addAction(self.export_all_animations_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = self.menuBar().addMenu("&View")
        self.fps_action = QAction("Show FPS Counter", self)
        self.fps_action.setCheckable(True)
        self.fps_action.setShortcut(QKeySequence("F3"))
        self.fps_action.setChecked(ModelViewport.show_fps)
        self.fps_action.toggled.connect(ModelViewport.set_show_fps_all)
        view_menu.addAction(self.fps_action)
        self.trilinear_action = QAction("Trilinear Texture Filtering (3D views)", self)
        self.trilinear_action.setCheckable(True)
        self.trilinear_action.setChecked(ModelViewport.trilinear)
        self.trilinear_action.setToolTip("Mipmapped minification for distant textures; off = bilinear only.")
        self.trilinear_action.toggled.connect(ModelViewport.set_trilinear_all)
        view_menu.addAction(self.trilinear_action)
        self.grid_action = QAction("Ground Grid (Character Preview)", self)
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(True)
        self.grid_action.toggled.connect(self._set_grid_visible)
        view_menu.addAction(self.grid_action)

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

        self.texture_frame_spin = QSpinBox()
        self.texture_frame_spin.setRange(0, 255)
        self.texture_frame_spin.setPrefix("Eye / colour frame ")
        self.texture_frame_spin.setToolTip("ROM texture-slot frame, wrapped per slot. Includes eye and clothing/colour slots; automatic blinking overrides eyes.")
        self.texture_frame_spin.valueChanged.connect(self._texture_frame_changed)
        layout.addWidget(self.texture_frame_spin)
        self.auto_blink_check = QCheckBox("Automatic eye blinking")
        self.auto_blink_check.setToolTip("Single-player Kong blink script with repeatable preview RNG.")
        self.auto_blink_check.toggled.connect(self._texture_frame_changed)
        layout.addWidget(self.auto_blink_check)
        self.mouth_label = QLabel("Mouth opening +0°")
        layout.addWidget(self.mouth_label)
        self.mouth_slider = QSlider(Qt.Orientation.Horizontal)
        self.mouth_slider.setRange(0, 35)
        self.mouth_slider.setToolTip("Custom jaw opening added to the selected animation. Zero keeps the ROM expression. Preview only; glTF exports keep the authored animation.")
        self.mouth_slider.valueChanged.connect(self._mouth_changed)
        layout.addWidget(self.mouth_slider)
        self.hair_check = QCheckBox("Tiny procedural hair (diagnostic)")
        self.hair_check.setToolTip("Game pendulum equations with clip-space head-Y as an anchor proxy; Actor speed/heading zero. Requires live capture for exact game motion.")
        self.hair_check.setVisible(self.source.character.key == "tiny")
        self.hair_check.toggled.connect(lambda _checked: self._select_animation())
        layout.addWidget(self.hair_check)
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
        self.mark_reference_button.hide()
        self.goto_reference_button.hide()
        self.reference_label.hide()
        self.animation_details_panel = QWidget()
        technical = QFormLayout(self.animation_details_panel)
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
        layout.addWidget(self.animation_details_panel)
        self.animation_details_panel.hide()
        self.timing_status_label.hide()

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
        for widget in (self.joint_combo, self.joint_value, self.joint_parent_value,
                       self.joint_position_value, self.joint_rest_value):
            debug_form.labelForField(widget).hide()
            widget.hide()
        layout.addLayout(debug_form)
        self.grid_check = QCheckBox("Show ground grid")
        self.grid_check.setChecked(True)
        self.grid_check.setToolTip("Ground plane at y = 0 with the X (red) and Z (blue) axes.")
        layout.addWidget(self.grid_check)
        self.reset_view_button = QPushButton("Reset View")
        layout.addWidget(self.reset_view_button)
        self.grid_check.hide()
        self.reset_view_button.hide()
        self.export_all_button = QPushButton("Export all animations...")
        self.export_all_button.setToolTip("Export the model plus every clip of the animation list "
                                          "(the 'Only ... clips' filter applies) as glTF files into one folder.")
        layout.addWidget(self.export_all_button)
        self.limitations_label = QLabel()
        self.limitations_label.setWordWrap(True)
        layout.addWidget(self.limitations_label)
        self.limitations_label.hide()
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
        self.grid_check.toggled.connect(self._set_grid_visible)
        self.export_all_button.clicked.connect(lambda _checked=False: self.export_all_animations())
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
        self.hair_check.setVisible(spec.key == "tiny")
        self.model_name_value.setText(spec.display_name)
        self._update_attachment()
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
        self.dk_only_check.setText(f"Only {short} clips (animation code + source calls)")
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
            "a fixed front camera. ROM combiner state with preview lighting; eye and colour frames are selectable; mouth opening is a custom preview offset.")

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

    def _update_attachment(self):
        if not hasattr(self, "viewport"):
            return
        from dataclasses import replace
        from . import static_model
        self._attachment_bind_positions = None
        self._bongo_animation = None
        if self.source.character.key == "dk" and self.source.character.variant == "instrument":
            model = static_model.actor_model(self.source.normalized, 0xA5, static_model.TextureCache(self.source.normalized))
            if model is not None:
                points = tuple(tuple(v * 1.25 for v in p) for p in model.render.positions)
                data = replace(model.render, positions=points)
                self._attachment_bind_positions = points
                from .bongos import animation
                self._bongo_animation = animation(self.source.normalized, model)
                self.viewport.set_attachment_data(data)
                return
        self.viewport.set_attachment_data(None)

    def _mouth_changed(self, *_args) -> None:
        self.mouth_label.setText(f"Mouth opening +{self.mouth_slider.value()}°")
        if not hasattr(self, "viewport"):
            return
        if self._selected_animation_id is not None:
            self._show_frame(self._current_frame)
        else:
            joint = next((bone.index for bone in self.source.skeleton.bones
                          if bone.master_index == 3), None)
            positions, skeleton = self.preview.bind_pose(mouth_joint=joint,
                                                         mouth_degrees=self.mouth_slider.value())
            self.viewport.set_scene_data(positions, skeleton)
            self.viewport.set_vertex_colors(self.preview.shade_colors())

    def _texture_frame_changed(self, *_args) -> None:
        from .core.actor_textures import KongBlink
        from .core.rom_model import parse_dynamic_textures
        slot_frames = None
        if self.auto_blink_check.isChecked():
            if not hasattr(self, "_blink") or self._blink.character != self.source.character.key:
                self._blink = KongBlink(self.source.character.key)
            slot_frames = self._blink.frames(getattr(self, "_blink_tick", 0), parse_dynamic_textures(self.source.actor))
        self.viewport.set_textures(self.preview.texture_frame(self.source, self.texture_frame_spin.value(), slot_frames=slot_frames))

    def _model_tab_changed(self, index: int) -> None:
        if index == 0:
            self.models_tab.pause()
        if index == 1:
            self._pause()
            self.models_tab.ensure_loaded()

    def _tab_changed(self, index: int) -> None:
        """Tabs load their data the first time they are opened (as in JFG Forge)."""
        widget = self.tabs.widget(index)
        if hasattr(widget, "ensure_loaded"):
            widget.ensure_loaded()
        if index != 0:
            self._pause()
        if widget is not self.model_tabs:
            self.models_tab.pause()
        if widget is not self.levels_tab:
            self.levels_tab.pause()
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
        corners = len(self.preview.render_data.positions)
        self.joint_geometry_value.setText(f"{corners // 3} faces ({corners} render corners)")

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
            if self.source.character.key == "tiny" and self.hair_check.isChecked() and kind in (ExportKind.ANIMATED, ExportKind.ANIMATION_ONLY):
                result = export_gltf(self.source, path, kind, animation_id=animation_id, procedural_hair=True)
            else:
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
        cache_key = (self.source.character.key, self.source.character.variant, animation_id, self.hair_check.isChecked() and self.source.character.key == "tiny")
        if animated and cache_key not in self._preview_cache:
            try:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                scene = PreviewScene.from_animation(self.source, animation_id, procedural_hair=cache_key[-1])
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
                else f"{name} (character-specific source call)" if descriptor.ownership.endswith("STATIC_SOURCE")
                else "DK runtime observed" if descriptor.ownership == "DK_OWNERSHIP_VERIFIED_RUNTIME"
                else f"not in {name}'s animation code; structurally compatible")
            self.animation_semantic_value.setText(descriptor.semantic_evidence)
            self.animation_prefix_value.setText(
                "included; Entry-4 scalar verified" if self._is_reference(animation_id)
                else "included; scalar is a diagnostic assumption")
            self.animation_context_value.setText(
                "Origin-centered; Tiny procedural hair diagnostic uses head-Y proxy; world speed/heading omitted; runtime_faithful=false"
                if self.preview.metadata.get("adjustments_applied") else
                "Origin-centered; Actor/world placement and adjustment rows omitted; runtime_faithful=false")
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
            self._texture_frame_changed()
            self._show_frame(self.preview.safe_first)
            self._start_playback()
        else:
            if self._attachment_bind_positions is not None:
                self.viewport.set_attachment_positions(self._attachment_bind_positions)
            self.playback_state_label.setText("Static")
            positions, skeleton = self.preview.bind_pose()
            self.viewport.set_scene_data(positions, skeleton)
            self._mouth_changed()
            self.viewport.set_vertex_colors(self.preview.shade_colors())
            self._last_skeleton = skeleton
            self.time_label.setText("Static canonical model")
            self.sample_label.setText(f"Hand-state mask {self.source.character.hand_mask}")
            self._update_joint_details()
            self._update_root_motion()
        self._update_timing_status()

    def _navigate_animation(self, offset: int) -> None:
        self.animation_combo.setCurrentIndex(
            (self.animation_combo.currentIndex() + offset) % self.animation_combo.count())

    def _set_grid_visible(self, visible: bool) -> None:
        self.viewport.set_grid_visible(visible)
        for control in (getattr(self, "grid_check", None), getattr(self, "grid_action", None)):
            if control is not None and control.isChecked() != visible:
                with QSignalBlocker(control):
                    control.setChecked(visible)

    def export_all_jobs(self):
        """(label, job) pairs: the static model and every listed clip (model + animation)."""
        spec = self.source.character
        source = self.source
        stem = f"{spec.key}" + ("" if spec.variant == "normal" else f"_{spec.variant}")
        hair = spec.key == "tiny" and self.hair_check.isChecked()
        jobs = [(f"{spec.display_name} model",
                 lambda folder: export_gltf(source, folder / f"{stem}_static_textured.gltf", ExportKind.STATIC_TEXTURED))]
        for row in range(self.animation_combo.count()):
            animation_id = self.animation_combo.itemData(row)
            if animation_id is None:
                continue
            label = self.animation_combo.itemText(row)
            name = f"{stem}_anim_{animation_id:04X}_" + "".join(
                c if c.isalnum() else "_" for c in label.split("—")[-1].strip())[:40].strip("_")

            def job(folder: Path, animation_id=animation_id, name=name):
                path = folder / f"{name}.gltf"
                if hair:
                    return export_gltf(source, path, ExportKind.ANIMATED, animation_id=animation_id, procedural_hair=True)
                if self._is_reference(animation_id):
                    return export_gltf(source, path, ExportKind.ANIMATED)
                return export_gltf(source, path, ExportKind.ANIMATED, animation_id=animation_id)
            jobs.append((label, job))
        return jobs

    def export_all_animations(self, folder: Path | None = None):
        from .batch_export import export_all
        self._pause()
        result = export_all(self, f"Export {self.source.character.display_name} animations",
                            self.export_all_jobs(), folder)
        if result is not None:
            self.statusBar().showMessage(result.summary(), 15000)
        return result

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
        mouth_joint = next((bone.index for bone in self.source.skeleton.bones
                            if bone.master_index == 3), None)
        positions, skeleton = self.preview.pose(self._current_frame,
                                               mouth_joint=mouth_joint,
                                               mouth_degrees=self.mouth_slider.value())
        self.viewport.set_scene_data(positions, skeleton)
        self.viewport.set_vertex_colors(self.preview.shade_colors())
        if self._attachment_bind_positions is not None:
            import numpy as np
            if self._bongo_animation is not None:
                seconds = (self._current_frame - first) / self._samples_per_second()
                bongo_frame = min(int(seconds * 30), len(self._bongo_animation.samples) - 1)
                points = np.asarray(self._bongo_animation.pose(bongo_frame)) * 1.25
            else:
                points = np.asarray(self._attachment_bind_positions)
            # Separate actor at the shared actor origin, not DK's pelvis joint.
            self.viewport.set_attachment_positions(tuple(tuple(float(v) for v in p) for p in points))
        if self.auto_blink_check.isChecked():
            self._blink_tick = int((self._time_seconds if self._playing else (self._current_frame - first) / self._samples_per_second()) * 30)
            self._texture_frame_changed()
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

    def closeEvent(self, event) -> None:
        # Qt close hides the window; its owned timers otherwise keep animating,
        # including when loading a replacement ROM window.
        self._pause()
        self.models_tab.pause()
        self.levels_tab.pause()
        super().closeEvent(event)

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
