"""环境监听：读数缓存、指纹变化后的 revision、以及线程不会拖垮进程。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from sprocket_mod_manager.infrastructure.environment_monitor import (
    EnvironmentMonitor,
    game_environment_fingerprint,
    mod_directory_signature,
)


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FingerprintTests(unittest.TestCase):
    def test_an_unconfigured_game_path_has_an_empty_fingerprint(self) -> None:
        self.assertEqual(game_environment_fingerprint(None), ())

    def test_the_fingerprint_notices_files_and_first_level_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            (game / "Sprocket_Data").mkdir(parents=True)
            (game / "Sprocket_Data" / "globalgamemanagers").write_bytes(b"0.2.53.2")
            (game / "Mods").mkdir()
            before = game_environment_fingerprint(game)

            (game / "Mods" / "Mod.dll").write_bytes(b"x" * 8)
            after = game_environment_fingerprint(game)

        self.assertNotEqual(before, after)

    def test_a_missing_directory_is_not_an_error(self) -> None:
        self.assertEqual(mod_directory_signature(Path("nowhere")), ())


class MonitorTests(unittest.TestCase):
    def test_the_snapshot_is_read_once_and_reused(self) -> None:
        calls: list[int] = []

        def read() -> dict:
            calls.append(1)
            return {"sprocket": {"state": "ok", "version": "0.2.53.2"}, "melonloader": {}}

        monitor = EnvironmentMonitor(read, lambda: ())
        first = monitor.snapshot()
        second = monitor.snapshot()

        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(first["revision"], 0)

    def test_invalidate_rereads_and_bumps_the_revision(self) -> None:
        payload = {"sprocket": {"state": "ok", "version": "0.2.53.2"}, "melonloader": {}}
        monitor = EnvironmentMonitor(lambda: dict(payload), lambda: ())

        monitor.snapshot()
        payload["sprocket"] = {"state": "ok", "version": "0.2.54.2"}
        monitor.invalidate()

        self.assertEqual(monitor.snapshot()["sprocket"]["version"], "0.2.54.2")
        self.assertEqual(monitor.snapshot()["revision"], 1)

    def test_noting_the_latest_loader_bumps_the_revision_once(self) -> None:
        monitor = EnvironmentMonitor(lambda: {"melonloader": {}}, lambda: ())

        monitor.note_latest_loader("0.7.3")
        monitor.note_latest_loader("0.7.3")

        snapshot = monitor.snapshot()
        self.assertEqual(snapshot["latest_loader"], "0.7.3")
        self.assertEqual(snapshot["revision"], 1)

    def test_a_failing_read_does_not_break_the_snapshot(self) -> None:
        def read() -> dict:
            raise OSError("boom")

        monitor = EnvironmentMonitor(read, lambda: ())

        self.assertEqual(monitor.snapshot()["melonloader"], {"installed": False, "version": None})

    def test_the_thread_bumps_the_revision_when_the_fingerprint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            game.mkdir()
            state = {"fingerprint": (1,)}
            monitor = EnvironmentMonitor(
                lambda: {"sprocket": {"state": "ok"}, "melonloader": {}},
                lambda: state["fingerprint"],
                interval=0.01,
                silence=0.01,
            )
            monitor.start()
            try:
                monitor.snapshot()
                state["fingerprint"] = (2,)

                changed = wait_for(lambda: monitor.snapshot()["revision"] > 0)
            finally:
                monitor.stop()

        self.assertTrue(changed, "指纹变了要重新读环境")

    def test_stopping_twice_is_harmless(self) -> None:
        monitor = EnvironmentMonitor(lambda: {}, lambda: ())
        monitor.start()
        monitor.stop()
        monitor.stop()


if __name__ == "__main__":
    unittest.main()
