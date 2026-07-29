import unittest
from pathlib import Path

from sprocket_mod_manager.dialogs import _dialog_layout, _release_modal_grab


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

    def test_modal_cleanup_releases_its_grab(self):
        class Dialog:
            released = False

            def grab_release(self):
                self.released = True

        dialog = Dialog()
        parent = type("Parent", (), {"grab_current": lambda _self: dialog})()

        _release_modal_grab(dialog, parent)

        self.assertTrue(dialog.released)

    def test_modal_cleanup_preserves_another_dialog_grab(self):
        class Dialog:
            released = False

            def grab_release(self):
                self.released = True

        dialog = Dialog()
        parent = type("Parent", (), {"grab_current": lambda _self: object()})()

        _release_modal_grab(dialog, parent)

        self.assertFalse(dialog.released)

    def test_modal_uses_native_toplevel_shell(self):
        source = (Path(__file__).parents[1] / "sprocket_mod_manager" / "dialogs.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("class ModalDialog(tk.Toplevel)", source)
        self.assertIn("def iconify(self)", source)


if __name__ == "__main__":
    unittest.main()
