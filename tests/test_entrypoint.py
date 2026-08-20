from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import modman
from sprocket_mod_manager.infrastructure.config import ConfigStore


class EntrypointTests(unittest.TestCase):
    def test_debug_only_argument_starts_gui_in_debug_mode(self) -> None:
        with (
            patch.object(modman.sys, "argv", ["modman.py", "--debug"]),
            patch("modman.configure_logging") as configure,
            patch("sprocket_mod_manager.presentation.webview_app.run_gui") as run_gui,
        ):
            result = modman.main()

        self.assertEqual(result, 0)
        self.assertTrue(configure.call_args.kwargs["debug"])
        run_gui.assert_called_once_with(modman.APP_VERSION, debug=True, debug_override=True)

    def test_config_debug_enables_gui_without_command_line_flag(self) -> None:
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            ConfigStore(app_dir).save({"debug": True})
            with (
                patch.object(modman.sys, "argv", ["modman.py"]),
                patch("modman.default_app_dir", return_value=app_dir),
                patch("modman.configure_logging") as configure,
                patch("sprocket_mod_manager.presentation.webview_app.run_gui") as run_gui,
            ):
                result = modman.main()

        self.assertEqual(result, 0)
        self.assertTrue(configure.call_args.kwargs["debug"])
        run_gui.assert_called_once_with(modman.APP_VERSION, debug=True, debug_override=False)

    def test_debug_argument_is_forwarded_to_cli(self) -> None:
        with (
            patch.object(modman.sys, "argv", ["modman.py", "--debug", "packages"]),
            patch("modman.configure_logging") as configure,
            patch("modman.cli_main", return_value=0) as cli_main,
        ):
            result = modman.main()

        self.assertEqual(result, 0)
        self.assertTrue(configure.call_args.kwargs["console"])
        cli_main.assert_called_once_with(["--debug", "packages"])

    def test_cli_marker_can_follow_debug_argument(self) -> None:
        with (
            patch.object(modman.sys, "argv", ["modman.py", "--debug", "--cli", "packages"]),
            patch("modman.configure_logging"),
            patch("modman.cli_main", return_value=0) as cli_main,
        ):
            result = modman.main()

        self.assertEqual(result, 0)
        cli_main.assert_called_once_with(["--debug", "packages"])


if __name__ == "__main__":
    unittest.main()
