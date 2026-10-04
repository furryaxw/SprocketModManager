from __future__ import annotations

import os
import signal
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.domain.errors import ScanError
from sprocket_mod_manager.utilities.package_paths import (
    file_type_is_wildcard,
    file_type_namespace,
    validate_file_type,
    validate_relative_path,
    validate_supply_type,
    validate_target,
)
from sprocket_mod_manager.utilities.processes import (
    _linux_executables,
    _terminate_wine,
    _wine_image,
    _wine_path,
    sprocket_is_running,
    terminate_sprocket,
)
from sprocket_mod_manager.utilities.ui_values import normalize_text_scale
from sprocket_mod_manager.utilities.urls import (
    is_loopback_host,
    normalize_github_proxy_url,
    normalize_proxy_url,
)

RUNNING_EXECUTABLES = "sprocket_mod_manager.utilities.processes.running_executables"
TASKKILL = "sprocket_mod_manager.utilities.processes.subprocess.run"
PROCESSES = "sprocket_mod_manager.utilities.processes"


class UtilityTests(unittest.TestCase):
    def test_loopback_detection_supports_names_and_ip_addresses(self) -> None:
        self.assertTrue(is_loopback_host("localhost"))
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertFalse(is_loopback_host("github.com"))

    def test_proxy_urls_are_normalized_by_purpose(self) -> None:
        self.assertEqual(normalize_proxy_url(" http://127.0.0.1:7890/ "), "http://127.0.0.1:7890")
        self.assertEqual(
            normalize_github_proxy_url("https://mirror.example/github"),
            "https://mirror.example/github/",
        )

    def test_package_paths_reject_escape_and_unknown_roots(self) -> None:
        self.assertEqual(validate_relative_path(r"Mods\Example.dll").as_posix(), "Mods/Example.dll")
        self.assertEqual(validate_target("UserLibs/Example.dll").parts[0], "UserLibs")
        with self.assertRaises(ScanError):
            validate_relative_path("../Example.dll")
        with self.assertRaises(ScanError):
            validate_target("Windows/System32.dll")

    def test_text_scale_is_clamped(self) -> None:
        self.assertEqual(normalize_text_scale(80), 100)
        self.assertEqual(normalize_text_scale(130), 130)
        self.assertEqual(normalize_text_scale(200), 160)

    def test_file_types_may_be_concrete_or_wildcards(self) -> None:
        self.assertEqual(validate_file_type(" melonloader:mod "), "melonloader:mod")
        self.assertEqual(validate_file_type("xunity:translation"), "xunity:translation")
        self.assertEqual(validate_file_type("melonloader:*"), "melonloader:*")
        for value in ("*", "*:mod", "melonloader:", ":mod", "melonloader", "melonloader:**"):
            with self.subTest(value=value):
                with self.assertRaises(ScanError):
                    validate_file_type(value)

    def test_supply_types_must_be_concrete(self) -> None:
        self.assertEqual(validate_supply_type("melonloader:mod"), "melonloader:mod")
        with self.assertRaises(ScanError):
            validate_supply_type("melonloader:*")

    def test_file_type_helpers_split_the_namespace(self) -> None:
        self.assertEqual(file_type_namespace("melonloader:mod"), "melonloader")
        self.assertTrue(file_type_is_wildcard("melonloader:*"))
        self.assertFalse(file_type_is_wildcard("melonloader:mod"))


class ProcessTests(unittest.TestCase):
    def test_sprocket_is_running_matches_the_game_directory(self) -> None:
        """同名进程装在别的目录不算这个游戏在跑；没配路径更不算。"""
        with patch(RUNNING_EXECUTABLES, return_value={1234: Path("D:/other/Sprocket.exe")}):
            self.assertFalse(sprocket_is_running(Path("C:/games/Sprocket")))
        with patch(
            RUNNING_EXECUTABLES,
            return_value={1234: Path("C:/games/Sprocket/Sprocket.exe")},
        ):
            self.assertTrue(sprocket_is_running(Path("C:/games/Sprocket")))
        self.assertFalse(sprocket_is_running(None))

    def test_terminate_ends_only_the_process_in_the_game_directory(self) -> None:
        game = Path("C:/games/Sprocket")
        running = {
            1234: Path("D:/other/Sprocket.exe"),
            5678: Path("C:/games/Sprocket/Sprocket.exe"),
        }
        with (
            patch(RUNNING_EXECUTABLES, return_value=running),
            patch(f"{PROCESSES}.os", SimpleNamespace(name="nt", path=os.path)),
            patch(TASKKILL, return_value=SimpleNamespace(returncode=0)) as taskkill,
        ):
            terminated = terminate_sprocket(game)

        self.assertEqual(terminated, [5678])
        self.assertEqual(taskkill.call_args.args[0][:3], ["taskkill", "/PID", "5678"])

    def test_terminate_reports_nothing_when_the_process_survives(self) -> None:
        game = Path("C:/games/Sprocket")
        running = {5678: Path("C:/games/Sprocket/Sprocket.exe")}
        with (
            patch(RUNNING_EXECUTABLES, return_value=running),
            patch(f"{PROCESSES}.os", SimpleNamespace(name="nt", path=os.path)),
            patch(TASKKILL, return_value=SimpleNamespace(returncode=1)),
        ):
            self.assertEqual(terminate_sprocket(game), [])

    def test_terminate_wine_asks_the_game_to_exit_first(self) -> None:
        with (
            patch(f"{PROCESSES}._process_alive", return_value=False),
            patch(f"{PROCESSES}.os.kill") as kill,
        ):
            self.assertTrue(_terminate_wine(5678))

        kill.assert_called_once_with(5678, signal.SIGTERM)

    @unittest.skipIf(os.name == "nt", "SIGKILL is a POSIX signal")
    def test_terminate_wine_forces_a_game_that_ignores_the_request(self) -> None:
        with (
            patch(f"{PROCESSES}._process_alive", side_effect=[True, False]),
            patch(f"{PROCESSES}._TERMINATE_WAIT_SECONDS", 0),
            patch(f"{PROCESSES}.os.kill") as kill,
        ):
            self.assertTrue(_terminate_wine(5678))

        self.assertEqual([item.args[1] for item in kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])

    @unittest.skipIf(os.name == "nt", "SIGKILL is a POSIX signal")
    def test_terminate_wine_reports_a_game_that_never_exits(self) -> None:
        with (
            patch(f"{PROCESSES}._process_alive", return_value=True),
            patch(f"{PROCESSES}._TERMINATE_WAIT_SECONDS", 0),
            patch(f"{PROCESSES}._FORCE_TERMINATE_WAIT_SECONDS", 0),
            patch(f"{PROCESSES}.os.kill"),
        ):
            self.assertFalse(_terminate_wine(5678))

    @unittest.skipIf(os.name == "nt", "wine processes are a Linux concern")
    def test_terminate_wine_really_ends_a_process(self) -> None:
        process = subprocess.Popen(["sleep", "30"])
        try:
            self.assertTrue(_terminate_wine(process.pid))
        finally:
            process.kill()
            process.wait()

    @unittest.skipIf(os.name == "nt", "the wine branch is Linux-only")
    def test_terminate_sprocket_uses_the_wine_branch(self) -> None:
        game = Path("/games/Sprocket")
        running = {5678: Path("/games/Sprocket/Sprocket.exe")}
        with (
            patch(RUNNING_EXECUTABLES, return_value=running),
            patch(f"{PROCESSES}._process_alive", return_value=False),
            patch(f"{PROCESSES}.os.kill") as kill,
        ):
            self.assertEqual(terminate_sprocket(game), [5678])

        kill.assert_called_once_with(5678, signal.SIGTERM)

    def test_wine_paths_from_a_proton_command_line(self) -> None:
        """Proton 的命令行里可能是 Z: 路径，或相对于 cwd 的名字。"""
        target = Path("/home/player/.steam/steamapps/common/Sprocket/Sprocket.exe")
        self.assertEqual(
            _wine_path(r"Z:\home\player\.steam\steamapps\common\Sprocket\Sprocket.exe", None), target,
        )
        self.assertEqual(_wine_path("Sprocket.exe", target.parent), target)
        self.assertIsNone(_wine_path(r"C:\Program Files (x86)\Sprocket\Sprocket.exe", None))
        self.assertIsNone(_wine_path("Sprocket.exe", None))

    @unittest.skipIf(os.name == "nt", "unix paths only mean something on Linux")
    def test_wine_path_accepts_a_unix_path(self) -> None:
        target = Path("/home/player/Sprocket/Sprocket.exe")
        self.assertEqual(_wine_path("/home/player/Sprocket/Sprocket.exe", None), target)

    def test_wine_image_reads_the_executable_out_of_a_command_line(self) -> None:
        cmdline = b"/usr/bin/wine64-preloader\0Z:\\home\\player\\Sprocket\\Sprocket.exe\0"
        self.assertEqual(
            _wine_image(cmdline, None, "sprocket.exe"),
            Path("/home/player/Sprocket/Sprocket.exe"),
        )
        self.assertIsNone(_wine_image(b"wine64-preloader\0--version\0", None, "sprocket.exe"))

    def test_proc_entries_are_matched_by_their_command_line(self) -> None:
        with TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "4242").mkdir()
            (proc / "4242" / "cmdline").write_bytes(
                b"/usr/bin/wine64-preloader\0Z:\\home\\player\\Sprocket\\Sprocket.exe\0"
            )
            (proc / "4243").mkdir()
            (proc / "4243" / "cmdline").write_bytes(b"wine64-preloader\0--version\0")
            (proc / "not-a-pid").mkdir()

            with patch(f"{PROCESSES}._PROC", proc):
                found = _linux_executables("Sprocket.exe")

        self.assertEqual(found, {4242: Path("/home/player/Sprocket/Sprocket.exe")})


if __name__ == "__main__":
    unittest.main()
