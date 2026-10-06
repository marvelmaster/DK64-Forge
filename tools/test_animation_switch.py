"""Animation reuse/parity and nonblocking latest-selection checks (local ROM required)."""
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from dk64_forge.preview_data import PreviewScene
from dk64_forge.session import load_rom
from dk64_forge.ui import MainWindow

ROM = Path("local/roms/dk64_us.n64")


@unittest.skipUnless(ROM.is_file(), "local supported ROM unavailable")
class AnimationSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.source = load_rom(ROM)

    def test_fast_clips_match_exported_poses_for_all_kongs(self):
        for key in ("dk", "diddy", "tiny", "chunky", "lanky"):
            source = self.source.for_character(key)
            base = PreviewScene.from_rom(source)
            for descriptor in (source.animations[0], next(d for d in source.animations if d.table11_id == source.character.default_animation)):
                with self.subTest(character=key, clip=descriptor.table11_id):
                    expected = PreviewScene.from_animation(source, descriptor.table11_id)
                    actual = base.with_animation(source, descriptor.table11_id)
                    self.assertIs(actual.bind_positions, base.bind_positions)
                    self.assertIs(actual.render_data, base.render_data)
                    self.assertEqual((actual.safe_first, actual.safe_last), (expected.safe_first, expected.safe_last))
                    np.testing.assert_array_equal(actual.local_samples, expected.local_samples)
                    for frame in (actual.safe_first, actual.safe_last):
                        p1, s1 = actual.pose(frame, mouth_joint=2, mouth_degrees=12)
                        p2, s2 = expected.pose(frame, mouth_joint=2, mouth_degrees=12)
                        np.testing.assert_array_equal(p1, p2)
                        self.assertEqual(s1, s2)

    def test_procedural_hair_and_interpolation_match_export_path(self):
        source = self.source.for_character("tiny")
        base = PreviewScene.from_rom(source)
        index = source.character.default_animation
        expected = PreviewScene.from_animation(source, index, procedural_hair=True)
        actual = base.with_animation(source, index, procedural_hair=True)
        np.testing.assert_array_equal(actual.local_samples, expected.local_samples)
        np.testing.assert_array_equal(actual.pose(actual.safe_first, fraction=.4)[0],
                                      expected.pose(expected.safe_first, fraction=.4)[0])

    def test_superseded_sampling_is_cancelled_for_generic_and_entry4_clips(self):
        base = PreviewScene.from_rom(self.source)
        for index in (self.source.animations[0].table11_id, 4):
            with self.subTest(index=index), self.assertRaisesRegex(RuntimeError, "superseded"):
                base.with_animation(self.source, index, cancelled=lambda: True)

    def test_rapid_navigation_stays_responsive_and_discards_stale_results(self):
        with TemporaryDirectory(dir="local_output") as directory:
            base = PreviewScene.from_rom(self.source)
            window = MainWindow(self.source, base, lambda p: None, state_directory=directory)
            release = threading.Event()
            original = PreviewScene.with_animation
            def delayed(scene, source, index, **kwargs):
                if not release.wait(5):
                    raise RuntimeError("test worker timeout")
                return original(scene, source, index, **kwargs)
            try:
                ticks = []
                timer = QTimer()
                timer.setInterval(5)
                timer.timeout.connect(lambda: ticks.append(1))
                timer.start()
                with patch.object(PreviewScene, "with_animation", delayed):
                    window.animation_combo.setCurrentIndex(0)
                    window._navigate_animation(1)
                    newest = window.animation_combo.currentData()
                    self.assertTrue(window._animation_loader.pending)
                    deadline = time.monotonic() + .05
                    while time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(.002)
                    self.assertGreater(len(ticks), 1)
                    release.set()
                    deadline = time.monotonic() + 10
                    while window._animation_loader.pending and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(.002)
                    self.assertFalse(window._animation_loader.pending)
                    self.assertEqual(window._selected_animation_id, newest)
                    self.assertEqual(window.preview.metadata["table11_id"], newest)
                    timer.stop()
                    # Reopening a prepared clip uses its cached scene immediately.
                    window._select_animation()
                    self.assertFalse(window._animation_loader.pending)
            finally:
                release.set()
                window.close()
                window._animation_loader.pool.waitForDone()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
