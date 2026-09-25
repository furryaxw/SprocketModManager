from __future__ import annotations

import contextlib
import io
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

            rotated = rotate_logs(app_dir, history_limit=5)

            self.assertEqual(rotated.read_text(encoding="utf-8"), "")
            histories = sorted(manager_history_dir(app_dir).glob("*.log"))
            self.assertEqual(len(histories), 5, "轮转后只保留 history_limit 条历史")
            self.assertTrue(any(path.read_text(encoding="utf-8") == "previous session" for path in histories),
                            "上一段日志要真的被搬进历史（不是被丢掉）")
            self.assertEqual(list(app_dir.glob("History-*.log")), [])

    def test_rotation_survives_a_locked_latest_log(self) -> None:
        """GUI 正开着 Latest.log 时命令行也要能跑：不轮转、不清空、更不能崩。"""
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            latest = manager_log_path(app_dir)
            latest.write_text("gui is writing here", encoding="utf-8")

            original_replace = Path.replace

            def locked(self: Path, target: Path) -> Path:
                if self == latest:
                    raise PermissionError(32, "The process cannot access the file")
                return original_replace(self, target)

            Path.replace = locked  # type: ignore[assignment]
            try:
                rotated = rotate_logs(app_dir)
            finally:
                Path.replace = original_replace  # type: ignore[assignment]

            self.assertEqual(rotated, latest)
            self.assertEqual(latest.read_text(encoding="utf-8"), "gui is writing here",
                             "锁住时不许清空别人正在写的日志")
            self.assertEqual(list(manager_history_dir(app_dir).glob("*.log")), [])

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

    def test_the_terminal_gets_the_records_that_go_into_the_file(self) -> None:
        """写进 Latest.log 的每一条同时也打到终端（窗口程序没有终端时只留文件）。"""
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            terminal = io.StringIO()
            with contextlib.redirect_stderr(terminal):
                configure_logging(app_dir, debug=False)
                logging.getLogger("test").info("mirrored-line")
                logging.shutdown()

            self.assertIn("mirrored-line", terminal.getvalue())
            self.assertIn("mirrored-line", manager_log_path(app_dir).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
