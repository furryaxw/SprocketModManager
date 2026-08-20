from __future__ import annotations

import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_mod_manager.infrastructure.app_logging import (
    configure_logging,
    manager_log_path,
    manager_history_dir,
    rotate_logs,
)


class AppLoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        root = logging.getLogger()
        for handler in tuple(root.handlers):
            if getattr(handler, "_sprocket_manager_handler", False):
                root.removeHandler(handler)
                handler.close()

    def test_startup_rotates_latest_and_keeps_five_histories(self) -> None:
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            for index in range(5):
                history_dir = manager_history_dir(app_dir)
                history_dir.mkdir(parents=True, exist_ok=True)
                history = history_dir / f"History-20260101-00000{index}-000000.log"
                history.write_text(str(index), encoding="utf-8")
                history.touch()
            latest = manager_log_path(app_dir)
            latest.write_text("previous session", encoding="utf-8")

            rotated = rotate_logs(app_dir)

            self.assertEqual(rotated.read_text(encoding="utf-8"), "")
            histories = list(manager_history_dir(app_dir).glob("History-*.log"))
            self.assertEqual(len(histories), 5)
            self.assertTrue(any(path.read_text(encoding="utf-8") == "previous session" for path in histories))
            self.assertEqual(list(app_dir.glob("History-*.log")), [])

    def test_debug_mode_controls_debug_records(self) -> None:
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            configure_logging(app_dir, debug=False)
            logging.getLogger("test").debug("hidden-debug")
            logging.getLogger("test").info("visible-info")
            logging.shutdown()
            normal = manager_log_path(app_dir).read_text(encoding="utf-8")

            configure_logging(app_dir, debug=True)
            logging.getLogger("test").debug("visible-debug")
            logging.shutdown()
            debug = manager_log_path(app_dir).read_text(encoding="utf-8")

            self.assertNotIn("hidden-debug", normal)
            self.assertIn("visible-info", normal)
            self.assertIn("visible-debug", debug)


if __name__ == "__main__":
    unittest.main()
