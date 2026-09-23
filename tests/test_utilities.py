from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.domain.errors import ScanError
from sprocket_mod_manager.utilities.package_paths import validate_relative_path, validate_target
from sprocket_mod_manager.utilities.processes import sprocket_is_running, terminate_sprocket
from sprocket_mod_manager.utilities.ui_values import normalize_text_scale
from sprocket_mod_manager.utilities.urls import (
    is_loopback_host,
    normalize_github_proxy_url,
    normalize_proxy_url,
)

RUNNING_EXECUTABLES = "sprocket_mod_manager.utilities.processes.running_executables"
TASKKILL = "sprocket_mod_manager.utilities.processes.subprocess.run"


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
        running = {
            1234: Path("D:/other/Sprocket.exe"),
            5678: Path("C:/games/Sprocket/Sprocket.exe"),
        }
        with (
            patch(RUNNING_EXECUTABLES, return_value=running),
            patch(TASKKILL, return_value=SimpleNamespace(returncode=0)) as taskkill,
        ):
            terminated = terminate_sprocket(Path("C:/games/Sprocket"))

        self.assertEqual(terminated, [5678])
        self.assertEqual(taskkill.call_args.args[0][:3], ["taskkill", "/PID", "5678"])

    def test_terminate_reports_nothing_when_the_process_survives(self) -> None:
        with (
            patch(
                RUNNING_EXECUTABLES,
                return_value={5678: Path("C:/games/Sprocket/Sprocket.exe")},
            ),
            patch(TASKKILL, return_value=SimpleNamespace(returncode=1)),
        ):
            self.assertEqual(terminate_sprocket(Path("C:/games/Sprocket")), [])


if __name__ == "__main__":
    unittest.main()
