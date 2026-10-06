"""Level selection regression checks: python -m unittest tools.test_level_clicks."""
from dataclasses import replace
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
import numpy as np
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication

from dk64_forge.level_selection import PlacementIndex
from dk64_forge.render_data import PreparedBatch, PreparedRenderData, PreparedTexture
from dk64_forge.viewport import ModelViewport
from dk64_forge.debug_view import PreparedSkeletonDebug


def triangle(z, *, alpha_mode="OPAQUE", alpha=255, double_sided=False, reversed=False, depth_write=True):
    points = ((-.5, -.5, z), (0., .5, z), (.5, -.5, z))
    if reversed:
        points = points[::-1]
    texture = PreparedTexture(0, 1, 1, bytes((255, 255, 255, alpha)), "CLAMP", "CLAMP")
    batch = PreparedBatch(0, 3, 0, double_sided, True, (1, 1, 1, 1), None,
                          alpha_mode=alpha_mode, depth_write=depth_write)
    return PreparedRenderData(points, ((.5, .5),)*3, (batch,), (texture,), (-.5, -.5, z), (.5, .5, z),
                              colors=((1, 1, 1, 1),)*3)


class ClickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.object = triangle(0)
        self.key = ("prop", 0)
        self.index = PlacementIndex([(SimpleNamespace(kind="prop", index=0), self.object)])
        self.camera = SimpleNamespace(projection_matrix=lambda aspect: np.eye(4),
                                      view_matrix=lambda: np.eye(4))

    def pick(self, wall=None, visible=None):
        return self.index.pick(self.object, self.camera, 50, 50, 100, 100,
                               {self.key} if visible is None else visible, wall)

    def test_visible_object_and_opaque_wall_occlusion(self):
        self.assertEqual(self.pick(), self.key)
        self.assertIsNone(self.pick(triangle(-.3)))
        self.assertIsNone(self.pick(visible=set()))

    def test_backfaces_and_transparent_cutouts_do_not_block_visible_objects(self):
        self.assertEqual(self.pick(triangle(-.3, reversed=True)), self.key)
        self.assertIsNone(self.pick(triangle(-.3, reversed=True, double_sided=True)))
        self.assertEqual(self.pick(triangle(-.3, alpha_mode="MASK", alpha=0)), self.key)
        self.assertIsNone(self.pick(triangle(-.3, alpha_mode="MASK", alpha=255)))
        self.assertEqual(self.pick(triangle(-.3, depth_write=False)), self.key)

    def test_transparent_parts_of_objects_are_not_selected(self):
        self.object = triangle(0, alpha_mode="MASK", alpha=0)
        self.assertIsNone(self.pick())

    def test_small_mouse_jitter_selects_without_orbiting_but_drag_does_not_select(self):
        widget = ModelViewport(self.object, PreparedSkeletonDebug((), (), ()))
        widget.resize(100, 100)
        spy = QSignalSpy(widget.object_clicked)
        before = np.asarray(widget._camera.view_matrix()).copy()
        QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))
        QTest.mouseMove(widget, QPoint(51, 51))
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(51, 51))
        self.assertEqual(spy.count(), 1)
        np.testing.assert_array_equal(before, widget._camera.view_matrix())
        QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))
        QTest.mouseMove(widget, QPoint(70, 70))
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(70, 70))
        self.assertEqual(spy.count(), 1)
        widget.close()

    def test_level_controls_order_and_removals(self):
        from dk64_forge.model_browser import ModelBrowserTab, KIND_MAP
        tab = ModelBrowserTab(b"", (KIND_MAP,), "Levels")
        layout = tab.content_check.parentWidget().layout()
        self.assertLess(layout.indexOf(tab.content_check), layout.indexOf(tab.interpolate_check))
        self.assertFalse(hasattr(tab, "night_check"))
        self.assertFalse(hasattr(tab, "fog_check"))
        tab.close()

    @unittest.skipUnless(Path("local/roms/dk64_us.n64").is_file(), "local ROM unavailable")
    def test_click_in_loaded_japes_selects_placed_object_in_list(self):
        from dk64_forge.session import load_rom
        from dk64_forge.model_browser import ModelBrowserTab, BrowserEntry, KIND_MAP
        source = load_rom(Path("local/roms/dk64_us.n64"))
        tab = ModelBrowserTab(source.normalized, (KIND_MAP,), "Levels")
        try:
            tab.content_check.setChecked(True)
            tab.show_entry(BrowserEntry(KIND_MAP, 7, "Japes", "ROM"))
            deadline = time.monotonic() + 30
            while tab._content_loader.pending and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertIsNotNone(tab._placement_index)
            view = tab.viewport
            view.resize(1000, 700)
            mvp = np.asarray(view._camera.projection_matrix(1000/700)) @ view._camera.view_matrix()
            points = np.asarray(tab._content.positions).reshape(-1, 3, 3).mean(axis=1)
            projected = np.c_[points, np.ones(len(points))] @ mvp.T
            projected = projected[projected[:, 3] > 0]
            projected = projected[:, :3] / projected[:, 3:]
            projected = projected[np.all(np.abs(projected) < 1, axis=1)]
            clicked = None
            for point in projected[::max(1, len(projected)//100)]:
                x, y = round((point[0]+1)*500), round((1-point[1])*350)
                key = tab._placement_index.pick(tab._content, view._camera, x, y, 1000, 700,
                                                 tab._visible_objects, view._data)
                if key is not None:
                    clicked = key
                    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
                    break
            self.assertIsNotNone(clicked, "No visible placed object found in the default Japes view")
            self.assertEqual([item.data(Qt.ItemDataRole.UserRole) for item in tab.objects_list.selectedItems()],
                             [clicked])
            self.assertIsNotNone(view.selection_points)
        finally:
            tab._content_loader.cancel()
            tab._content_loader.pool.waitForDone()
            tab.close()


if __name__ == "__main__":
    unittest.main()
