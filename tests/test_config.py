import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.config import (
    ConfigStore,
    DEFAULT_GITHUB_PROXY_URL,
    DEFAULT_PROXY_URL,
    detect_game_path,
    effective_game_path,
    effective_github_proxy_url,
    effective_index_url,
    effective_proxy_url,
    language_from_locale_name,
    normalize_github_proxy_url,
    normalize_proxy_url,
    normalize_text_scale,
)
from sprocket_mod_manager.service import DEFAULT_INDEX_URL


class ConfigTests(unittest.TestCase):
    def test_default_index_uses_public_custom_domain(self):
        self.assertEqual(
            DEFAULT_INDEX_URL,
            "https://sprocketmods.furryaxw.top/index.json",
        )

    def test_language_name_handles_windows_chinese_locale(self):
        self.assertEqual(language_from_locale_name("Chinese (Simplified)_China"), "zh")
        self.assertEqual(language_from_locale_name("zh-CN"), "zh")
        self.assertEqual(language_from_locale_name("English_United States"), "en")

    def test_new_config_uses_automatic_language_mode(self):
        with TemporaryDirectory() as temporary:
            with patch("sprocket_mod_manager.config.detect_game_path", return_value=""):
                config = ConfigStore(Path(temporary)).load()
        self.assertEqual(config["language"], "auto")

    def test_text_scale_defaults_and_clamps_to_accessible_range(self):
        with TemporaryDirectory() as temporary:
            store = ConfigStore(Path(temporary))
            self.assertEqual(store.load()["text_scale"], 100)

            store.save({"text_scale": 500})
            self.assertEqual(store.load()["text_scale"], 160)

            store.save({"text_scale": "invalid"})
            self.assertEqual(store.load()["text_scale"], 100)

        self.assertEqual(normalize_text_scale(130), 130)
        self.assertEqual(normalize_text_scale(80), 100)

    def test_new_config_keeps_automatic_sources_empty(self):
        with TemporaryDirectory() as temporary:
            config = ConfigStore(Path(temporary)).load()
        self.assertEqual(config["game_path"], "")
        self.assertEqual(config["index_url"], "")
        self.assertFalse(config["proxy_enabled"])
        self.assertEqual(config["proxy_url"], "")
        self.assertFalse(config["github_proxy_enabled"])
        self.assertEqual(config["github_proxy_url"], "")

    def test_network_urls_are_normalized_and_validated(self):
        self.assertEqual(normalize_proxy_url(" http://127.0.0.1:7890/ "), "http://127.0.0.1:7890")
        self.assertEqual(
            normalize_github_proxy_url("https://mirror.example.com/github"),
            "https://mirror.example.com/github/",
        )
        with self.assertRaises(ValueError):
            normalize_proxy_url("socks5://127.0.0.1:1080")
        with self.assertRaises(ValueError):
            normalize_github_proxy_url("http://mirror.example.com/")

    def test_enabled_network_settings_use_defaults_for_empty_addresses(self):
        self.assertEqual(effective_proxy_url({"proxy_enabled": True}), DEFAULT_PROXY_URL)
        self.assertEqual(
            effective_github_proxy_url({"github_proxy_enabled": True}),
            DEFAULT_GITHUB_PROXY_URL,
        )
        self.assertEqual(effective_proxy_url({"proxy_url": DEFAULT_PROXY_URL}), "")
        self.assertEqual(
            effective_github_proxy_url({"github_proxy_url": DEFAULT_GITHUB_PROXY_URL}),
            "",
        )

    def test_empty_sources_resolve_to_automatic_defaults(self):
        with patch("sprocket_mod_manager.config.detect_game_path", return_value="G:/Steam/Sprocket"):
            self.assertEqual(effective_game_path({"game_path": "  "}), "G:/Steam/Sprocket")
        self.assertEqual(effective_index_url({"index_url": "  "}), DEFAULT_INDEX_URL)

    def test_explicit_sources_override_automatic_defaults(self):
        with patch("sprocket_mod_manager.config.detect_game_path") as detect:
            self.assertEqual(
                effective_game_path({"game_path": " D:/Games/Sprocket "}),
                "D:/Games/Sprocket",
            )
        detect.assert_not_called()
        self.assertEqual(
            effective_index_url({"index_url": " index.json "}),
            "index.json",
        )

    def test_detect_game_path_reads_sprocket_steam_manifest(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            steam_root = root / "Steam"
            library = root / "SteamLibrary"
            (steam_root / "steamapps").mkdir(parents=True)
            (steam_root / "steamapps" / "libraryfolders.vdf").write_text(
                '\n'.join(
                    [
                        '"libraryfolders"',
                        "{",
                        '\t"1"',
                        "\t{",
                        f'\t\t"path"\t\t"{str(library).replace(chr(92), chr(92) * 2)}"',
                        "\t}",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )
            steamapps = library / "steamapps"
            steamapps.mkdir(parents=True)
            (steamapps / "appmanifest_1674170.acf").write_text(
                '"AppState"\n{\n\t"appid"\t\t"1674170"\n\t"installdir"\t\t"Sprocket Test"\n}\n',
                encoding="utf-8",
            )
            game_path = steamapps / "common" / "Sprocket Test"
            game_path.mkdir(parents=True)
            (game_path / "Sprocket.exe").touch()

            with (
                patch("sprocket_mod_manager.config._steam_install_roots", return_value=(steam_root,)),
                patch("sprocket_mod_manager.config.Path.cwd", return_value=root / "not-the-game"),
            ):
                detected = detect_game_path()

        self.assertEqual(detected, str(game_path.resolve()))
