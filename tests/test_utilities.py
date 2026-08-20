from __future__ import annotations

import unittest

from sprocket_mod_manager.domain.errors import ScanError
from sprocket_mod_manager.utilities.package_paths import validate_relative_path, validate_target
from sprocket_mod_manager.utilities.ui_values import normalize_text_scale
from sprocket_mod_manager.utilities.urls import (
    is_loopback_host,
    normalize_github_proxy_url,
    normalize_proxy_url,
)


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


if __name__ == "__main__":
    unittest.main()
