import unittest
from pathlib import Path

from sprocket_mod_manager.dialogs import _dialog_layout


class DialogTests(unittest.TestCase):
    def test_short_messages_use_compact_dialog(self):
        height, scrollable = _dialog_layout("A short confirmation message.")
        self.assertFalse(scrollable)
        self.assertGreaterEqual(height, 230)

    def test_long_messages_use_scrolling_dialog(self):
        height, scrollable = _dialog_layout("\n".join(f"Package {index}" for index in range(20)))
        self.assertTrue(scrollable)
        self.assertEqual(height, 390)

    def test_gui_does_not_use_native_message_boxes(self):
        source = (Path(__file__).parents[1] / "sprocket_mod_manager" / "gui.py").read_text(encoding="utf-8")
        self.assertNotIn("messagebox", source)
        self.assertIn("ask_confirmation", source)
        self.assertIn("show_message", source)


if __name__ == "__main__":
    unittest.main()
