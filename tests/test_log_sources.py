"""日志来源：标识符按运行时布局给出日志路径，API 按「文件在不在」决定可不可用。"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sprocket_mod_manager.infrastructure.config import ConfigStore
from sprocket_mod_manager.infrastructure.log_upload import LogUploadResult
from sprocket_mod_manager.presentation.web_gui import ClientApi

FIXTURE_MOD = (
    Path(__file__).resolve().parent / "fixtures" / "dll_metadata" / "dll" / "FixtureMod.dll"
)


def sprocket_game(root: Path) -> Path:
    game = root / "game"
    game.mkdir()
    (game / "Sprocket.exe").touch()
    return game


def melonloader_install(game: Path) -> None:
    """原生 MelonLoader 布局：根目录代理 + net6 运行时 DLL。"""
    (game / "version.dll").write_bytes(b"proxy")
    (game / "MelonLoader" / "net6").mkdir(parents=True)
    shutil.copyfile(FIXTURE_MOD, game / "MelonLoader" / "net6" / "MelonLoader.dll")


def bepinex_install(game: Path) -> None:
    (game / "winhttp.dll").write_bytes(b"proxy")
    (game / "BepInEx" / "core").mkdir(parents=True)
    shutil.copyfile(FIXTURE_MOD, game / "BepInEx" / "core" / "BepInEx.Core.dll")


def bridged_install(game: Path) -> None:
    """桥接布局：运行时安家在 `MLLoader/`，自己不写日志。"""
    (game / "MLLoader" / "MelonLoader" / "net6").mkdir(parents=True)
    shutil.copyfile(FIXTURE_MOD, game / "MLLoader" / "MelonLoader" / "net6" / "MelonLoader.dll")


class LogSourceTests(unittest.TestCase):
    def _api(self, root: Path, game: Path, *, configured: bool = True) -> ClientApi:
        app_dir = root / "app"
        ConfigStore(app_dir).save(
            {"language": "en", "game_path": str(game) if configured else "", "index_url": ""}
        )
        return ClientApi("test", app_dir=app_dir)

    def _sources(self, api: ClientApi) -> list[dict]:
        result = api.get_log_sources()
        self.assertTrue(result["ok"])
        return result["sources"]

    def test_a_melonloader_install_offers_its_latest_log(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = sprocket_game(root)
            melonloader_install(game)
            (game / "MelonLoader" / "Latest.log").write_text("ready", encoding="utf-8")
            api = self._api(root, game)
            try:
                sources = self._sources(api)
            finally:
                api.install_queue.close()

        self.assertEqual([source["id"] for source in sources], ["manager", "melonloader"])
        self.assertEqual(sources[0]["kind"], "manager")
        self.assertTrue(sources[0]["available"])
        self.assertEqual(sources[1]["kind"], "loader")
        self.assertEqual(sources[1]["loader"], "MelonLoader")
        self.assertEqual(sources[1]["path"], "MelonLoader/Latest.log")
        self.assertTrue(sources[1]["available"])

    def test_a_bepinex_install_offers_its_log_output(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = sprocket_game(root)
            bepinex_install(game)
            (game / "BepInEx" / "LogOutput.log").write_text("ready", encoding="utf-8")
            api = self._api(root, game)
            try:
                sources = self._sources(api)
            finally:
                api.install_queue.close()

        self.assertEqual([source["id"] for source in sources], ["manager", "bepinex"])
        loader = sources[1]
        self.assertEqual(loader["loader"], "BepInEx")
        self.assertEqual(loader["path"], "BepInEx/LogOutput.log")
        self.assertTrue(loader["available"])

    def test_the_bridge_alone_offers_no_available_loader_log(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = sprocket_game(root)
            bridged_install(game)
            api = self._api(root, game)
            try:
                sources = self._sources(api)
            finally:
                api.install_queue.close()

        self.assertEqual(
            [source["id"] for source in sources if source["available"]], ["manager"]
        )
        loader = next(source for source in sources if source["kind"] == "loader")
        self.assertEqual(loader["path"], "MLLoader/MelonLoader/Latest.log")
        self.assertFalse(loader["available"])

    def test_bepinex_beside_the_bridge_offers_only_the_bepinex_log(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = sprocket_game(root)
            bridged_install(game)
            bepinex_install(game)
            (game / "BepInEx" / "LogOutput.log").write_text("ready", encoding="utf-8")
            api = self._api(root, game)
            try:
                sources = self._sources(api)
            finally:
                api.install_queue.close()

        available = {source["id"] for source in sources if source["available"]}
        self.assertEqual(available, {"manager", "bepinex"})

    def test_without_a_configured_game_only_the_manager_log_is_listed(self) -> None:
        with TemporaryDirectory() as directory, patch(
            # 配置里没写游戏路径时后端会去自动探测；这台机器上装着游戏也一样，
            # 所以这一条要把探测按住，测的才是「没有游戏」。
            "sprocket_mod_manager.presentation.web_gui.effective_game_path",
            return_value="",
        ):
            root = Path(directory)
            game = sprocket_game(root)
            api = self._api(root, game, configured=False)
            try:
                sources = self._sources(api)
            finally:
                api.install_queue.close()

        self.assertEqual([source["id"] for source in sources], ["manager"])

    def test_upload_log_reads_the_selected_loader_log(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = sprocket_game(root)
            melonloader_install(game)
            log = game / "MelonLoader" / "Latest.log"
            log.write_text("ready", encoding="utf-8")
            api = self._api(root, game)
            uploaded = LogUploadResult("request", 201, 5, "https://logs.example/result")
            try:
                with patch(
                    "sprocket_mod_manager.presentation.web_gui.upload_log_file",
                    return_value=uploaded,
                ) as upload:
                    result = api.upload_log("melonloader")
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], uploaded.url)
        self.assertEqual(upload.call_args.args[0], log)

    def test_upload_log_rejects_an_unknown_id(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = sprocket_game(root)
            api = self._api(root, game)
            try:
                with patch(
                    "sprocket_mod_manager.presentation.web_gui.upload_log_file"
                ) as upload:
                    result = api.upload_log("no-such-source")
            finally:
                api.install_queue.close()

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "log_source_unknown")
        upload.assert_not_called()

    def test_upload_log_rejects_a_source_that_is_not_on_disk(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = sprocket_game(root)
            bridged_install(game)
            api = self._api(root, game)
            try:
                with patch(
                    "sprocket_mod_manager.presentation.web_gui.upload_log_file"
                ) as upload:
                    result = api.upload_log("melonloader")
            finally:
                api.install_queue.close()

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "log_source_unavailable")
        upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
