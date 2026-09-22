"""AppData 纯净度：管理器目录只放管理器自己的配置，**禁止**堆游戏目录里的东西。

AppData 里只能存管理器自身的配置（config.json、网络缓存、日志、WebView 存储）。凡是"从游戏目录读出来的"或"要写回游戏目录的"——安装记录、DLL 元数据缓存、
被覆盖文件的备份——都必须留在 `<game>/SprocketModManager/`。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.application.service import ModManagerService  # noqa: E402
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402
from sprocket_mod_manager.infrastructure.config import ConfigStore  # noqa: E402
from sprocket_mod_manager.presentation.web_gui import ClientApi  # noqa: E402

from test_adoption import FIXTURE_MOD, package  # noqa: E402

# 这些名字一旦出现在 AppData 里就说明有游戏目录的东西被写错了地方。
FORBIDDEN_IN_APPDATA = (
    "installed.json", "metadata-cache.json", "file-metadata.json", "transactions", "backups", "profiles",
)


class AppDataPurityTests(unittest.TestCase):
    def _api(self, root: Path) -> tuple[ClientApi, Path, Path, ModManagerService]:
        app_dir = root / "app"
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        shutil.copyfile(FIXTURE_MOD, game / "Mods" / "FixtureMod.dll")
        ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
        service = ModManagerService(app_dir)
        service.registry = Registry([
            package("fixture.sprocket-mod", "FixtureMod.dll", FIXTURE_MOD.read_bytes(),
                    repository="fixture/FixtureMod"),
        ])
        api = ClientApi("0.4.2", app_dir=app_dir, service_factory=lambda _app_dir: service)
        return api, app_dir, game, service

    def test_game_derived_state_never_lands_in_appdata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, app_dir, game, _service = self._api(Path(directory))
            try:
                self.assertTrue(api.get_installed()["ok"])
                self.assertTrue(api.adopt_existing()["ok"])
                self.assertTrue(api.verify_installed()["ok"])
            finally:
                api.install_queue.close()

            state_dir = game / "SprocketModManager"
            state_file = state_dir / "installed.json"
            self.assertTrue(state_file.is_file(), "安装记录在游戏目录")
            # DLL 元数据**独立成文件**且**按内容 hash 键**：
            # files{路径: {size, mtime, hash}} + meta{hash: 解析结果}。
            metadata_file = state_dir / "file-metadata.json"
            self.assertTrue(metadata_file.is_file(), "DLL 元数据缓存在独立文件里")
            cached = json.loads(metadata_file.read_text(encoding="utf-8"))
            self.assertTrue(cached.get("meta"), "扫描过的 DLL 元数据必须真的写进去了")
            self.assertTrue(cached.get("files"), "要能从路径查到内容 hash")
            for entry in cached["files"].values():
                self.assertIn("hash", entry)
                self.assertIn(entry["hash"], cached["meta"], "files 里的 hash 必须能在 meta 段找到")
            stored = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertNotIn("metadata", stored, "installed.json 里没有 DLL 元数据段")

            for name in FORBIDDEN_IN_APPDATA:
                self.assertFalse((app_dir / name).exists(),
                                 f"{name} 不该出现在 AppData（游戏目录相关的东西必须跟着游戏走）")

    def test_backups_of_replaced_files_go_to_the_game_backup_dir(self) -> None:
        """替换一个已有 DLL 时，被覆盖的文件必须备份在 <game>/SprocketModManager/backup。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            game = root / "game"
            (game / "Mods").mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            target = game / "Mods" / "FixtureMod.dll"
            target.write_bytes(b"original payload")
            ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
            service = ModManagerService(app_dir)
            installer = service._installer_for(game)

            from sprocket_mod_manager.infrastructure.file_transaction import FileTransaction

            transaction = FileTransaction(game / "SprocketModManager")
            try:
                transaction.backup_file(target, game)
                backups = list((game / "SprocketModManager" / "backup").rglob("FixtureMod.dll"))
                self.assertEqual(len(backups), 1, "备份必须落在游戏目录的 backup/ 下")
                self.assertEqual(backups[0].read_bytes(), b"original payload")
            finally:
                transaction.close()

            self.assertFalse((app_dir / "transactions").exists(), "事务目录不该出现在 AppData")
            self.assertIsNotNone(installer)


if __name__ == "__main__":
    unittest.main()
