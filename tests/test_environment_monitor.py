"""环境监听：读数缓存、指纹变化后的 revision、以及线程不会拖垮进程。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from sprocket_mod_manager.application.identifiers import mod_directory_paths
from sprocket_mod_manager.domain.models import RegistryPackage
from sprocket_mod_manager.infrastructure.environment_monitor import (
    EnvironmentMonitor,
    game_environment_fingerprint,
    mod_directory_signature,
)

BRIDGE_ID = "1499501762.bepinex-melonloader-loader"


def bridge_package() -> RegistryPackage:
    return RegistryPackage(
        id=BRIDGE_ID,
        name="BepInEx.MelonLoader.Loader",
        authors=("1499501762",),
        repository="1499501762/BepInEx.MelonLoader.Loader",
        license="Apache-2.0",
        display_name={"en": "MLLoader"},
        description={"en": "bridge"},
        release={},
        dependencies=(),
        install={},
        category="utility",
        tags=(),
        kind="loaderbridge",
        provides={"lavagang.melonloader": "0.7.3"},
        supply={
            "melonloader:core": "{Sprocket}/MLLoader/MelonLoader",
            "melonloader:mod": "{Sprocket}/MLLoader/Mods",
            "melonloader:plugin": "{Sprocket}/MLLoader/Plugins",
            "melonloader:userlib": "{Sprocket}/MLLoader/UserLibs",
        },
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

    def test_a_bridge_mod_appearing_changes_the_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            (game / "MLLoader" / "Mods").mkdir(parents=True)
            directories = mod_directory_paths(game, [bridge_package()], (BRIDGE_ID,))
            self.assertIn("MLLoader/Mods", directories)

            before = game_environment_fingerprint(game, directories)
            (game / "MLLoader" / "Mods" / "Bridge.dll").write_bytes(b"x" * 8)
            after = game_environment_fingerprint(game, directories)

        self.assertNotEqual(before, after)


class MonitorTests(unittest.TestCase):
    def test_the_snapshot_is_read_once_and_reused(self) -> None:
        calls: list[int] = []

        def read() -> dict:
            calls.append(1)
            return {"sprocket": {"state": "ok", "version": "0.2.53.2"}, "loaders": {}}

        monitor = EnvironmentMonitor(read, lambda: ())
        first = monitor.snapshot()
        second = monitor.snapshot()

        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(first["revision"], 0)

    def test_invalidate_rereads_and_bumps_the_revision(self) -> None:
        payload = {"sprocket": {"state": "ok", "version": "0.2.53.2"}, "loaders": {}}
        monitor = EnvironmentMonitor(lambda: dict(payload), lambda: ())

        monitor.snapshot()
        payload["sprocket"] = {"state": "ok", "version": "0.2.54.2"}
        monitor.invalidate()

        self.assertEqual(monitor.snapshot()["sprocket"]["version"], "0.2.54.2")
        self.assertEqual(monitor.snapshot()["revision"], 1)

    def test_noting_the_latest_loaders_bumps_the_revision_once(self) -> None:
        monitor = EnvironmentMonitor(lambda: {"loaders": {}}, lambda: ())

        monitor.note_latest_loaders({"lavagang.melonloader": "0.7.3"})
        monitor.note_latest_loaders({"lavagang.melonloader": "0.7.3"})

        snapshot = monitor.snapshot()
        self.assertEqual(snapshot["latest_loaders"], {"lavagang.melonloader": "0.7.3"})
        self.assertEqual(snapshot["revision"], 1)

    def test_a_failing_read_does_not_break_the_snapshot(self) -> None:
        def read() -> dict:
            raise OSError("boom")

        monitor = EnvironmentMonitor(read, lambda: ())

        self.assertEqual(monitor.snapshot()["loaders"], {})
        self.assertEqual(monitor.snapshot()["sprocket"], {})

    def test_the_thread_bumps_the_revision_when_the_fingerprint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            game.mkdir()
            state = {"fingerprint": (1,)}
            monitor = EnvironmentMonitor(
                lambda: {"sprocket": {"state": "ok"}, "loaders": {}},
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
