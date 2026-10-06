"""ROM chooser preferences and session-menu removal: python -m unittest tools.test_rom_dialog."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from dk64_forge import ui


class RomDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory(dir="local_output")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.settings_file = str(self.root / "settings.ini")
        self.settings = QSettings(self.settings_file, QSettings.Format.IniFormat)
        self.settings_patch = patch.object(ui, "_rom_dialog_settings", return_value=self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def test_remembered_folder_survives_reopened_settings_and_dialog_cancel(self):
        rom_dir = self.root / "ROMs"
        rom_dir.mkdir()
        ui._remember_rom_directory(rom_dir / "game.z64")
        reopened = QSettings(self.settings_file, QSettings.Format.IniFormat)
        self.assertEqual(reopened.value("last_rom_directory"), str(rom_dir))
        with patch.object(ui.QFileDialog, "getOpenFileName", return_value=("", "")) as dialog:
            self.assertIsNone(ui._select_rom(None))
        self.assertEqual(dialog.call_args.args[2], str(rom_dir))
        self.assertEqual(self.settings.value("last_rom_directory"), str(rom_dir))
        self.assertEqual(dialog.call_args.kwargs, {})  # Keep Qt's native Windows dialog.

    def test_missing_folder_falls_back_and_selected_path_is_returned(self):
        self.settings.setValue("last_rom_directory", str(self.root / "removed"))
        selected = str(self.root / "other.n64")
        with patch.object(ui.QFileDialog, "getOpenFileName", return_value=(selected, "")) as dialog:
            self.assertEqual(ui._select_rom(None), Path(selected))
        self.assertEqual(dialog.call_args.args[2], "")
        # Selecting a file alone does not replace the last successfully loaded folder.
        self.assertEqual(self.settings.value("last_rom_directory"), str(self.root / "removed"))

    def test_first_use_has_no_forced_directory(self):
        with patch.object(ui.QFileDialog, "getOpenFileName", return_value=("", "")) as dialog:
            self.assertIsNone(ui._select_rom(None))
        self.assertEqual(dialog.call_args.args[2], "")

    @unittest.skipUnless(Path("local/roms/dk64_us.n64").is_file(), "local ROM unavailable")
    def test_loaded_window_has_no_session_menu_or_automatic_session_writes(self):
        from dk64_forge.preview_data import PreviewScene
        from dk64_forge.session import load_rom
        import json

        source = load_rom(Path("local/roms/dk64_us.n64"))
        session_file = self.root / (source.sha256 + "_session.json")
        session_file.write_text("old session must not be read", encoding="utf-8")
        preferences = self.root / "preferences.json"
        preferences.write_text(json.dumps({"remember_session": True}), encoding="utf-8")
        window = ui.MainWindow(source, PreviewScene.from_rom(source), lambda p: None,
                               state_directory=self.root)
        try:
            self.app.processEvents()
            self.assertNotIn("Session", [action.text() for action in window.menuBar().actions()])
            self.assertFalse(hasattr(window, "_session_restore"))
        finally:
            window.close()
            self.app.processEvents()
        self.assertEqual(session_file.read_text(encoding="utf-8"), "old session must not be read")
        self.assertEqual(json.loads(preferences.read_text(encoding="utf-8")), {"remember_session": True})


if __name__ == "__main__":
    unittest.main()
