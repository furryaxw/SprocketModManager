"""禁用/启用按**包**生效：该包在 `Mods`/`Plugins` 下的全部可执行文件一起切换，`UserLibs` 不动。

禁用 = 禁用属于这个 package 的所有可执行文件。
`UserLibs` 是别的模组引用的库，就地改名会连累依赖者，所以明确排除。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.application.service import ModManagerService  # noqa: E402
from sprocket_mod_manager.infrastructure.config import ConfigStore  # noqa: E402
from sprocket_mod_manager.presentation.web_gui import ClientApi  # noqa: E402

from test_adoption import FIXTURE_MOD  # noqa: E402


class PackageWideToggleTests(unittest.TestCase):
    def _api(self, root: Path) -> tuple[ClientApi, Path]:
        app_dir = root / "app"
        game = root / "game"
        for kind in ("Mods", "Plugins", "UserLibs"):
            (game / kind).mkdir(parents=True, exist_ok=True)
        (game / "Sprocket.exe").touch()
        shutil.copyfile(FIXTURE_MOD, game / "Mods" / "Alpha.dll")
        shutil.copyfile(FIXTURE_MOD, game / "Plugins" / "Beta.dll")
        shutil.copyfile(FIXTURE_MOD, game / "UserLibs" / "Lib.dll")
        ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
        service = ModManagerService(app_dir)
        store = service._installer_for(game).state_store
        files = ["Mods/Alpha.dll", "Plugins/Beta.dll", "UserLibs/Lib.dll"]
        store.save({
            "schema_version": 2,
            "packages": {
                "fixture.multi": {
                    "name": "Multi File Mod", "version": "1.0.0", "requested": True,
                    "dependencies": [], "install_mode": "standard", "files": files,
                }
            },
            "files": {item: {"owners": ["fixture.multi"], "sha256": ""} for item in files},
        })
        api = ClientApi("0.4.2", app_dir=app_dir, service_factory=lambda _app_dir: service)
        return api, game

    def test_disable_toggles_mods_and_plugins_but_leaves_userlibs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game = self._api(Path(directory))
            try:
                result = api.toggle_mod("Mods/Alpha.dll", False)
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            self.assertEqual(sorted(result["toggled_paths"]),
                             ["Mods/Alpha.dll.disable", "Plugins/Beta.dll.disable"],
                             "同一个包在 Mods 与 Plugins 下的文件都要禁用")
            self.assertTrue((game / "Mods" / "Alpha.dll.disable").is_file())
            self.assertTrue((game / "Plugins" / "Beta.dll.disable").is_file())
            self.assertTrue((game / "UserLibs" / "Lib.dll").is_file(),
                            "UserLibs 是被引用的库，禁用包不许动它")
            self.assertFalse((game / "UserLibs" / "Lib.dll.disable").exists())

    def test_enable_restores_every_file_of_the_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game = self._api(Path(directory))
            try:
                api.toggle_mod("Mods/Alpha.dll", False)
                result = api.toggle_mod("Plugins/Beta.dll.disable", True)
                installed = api.get_installed()
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            self.assertTrue((game / "Mods" / "Alpha.dll").is_file())
            self.assertTrue((game / "Plugins" / "Beta.dll").is_file())
            rows = {row["path"]: row["disabled"] for row in installed["local_mods"]}
            self.assertFalse(rows.get("Mods/Alpha.dll", True), "启用后 Alpha 不再标记为禁用")
            self.assertFalse(rows.get("Plugins/Beta.dll", True), "启用后 Beta 不再标记为禁用")

    def test_local_only_mod_toggles_only_itself(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game = self._api(Path(directory))
            try:
                # 没有归属的本地 DLL：只切它自己，不去猜别的文件。
                shutil.copyfile(FIXTURE_MOD, game / "Mods" / "Loose.dll")
                result = api.toggle_mod("Mods/Loose.dll", False)
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["toggled_paths"], ["Mods/Loose.dll.disable"])
            self.assertTrue((game / "Mods" / "Alpha.dll").is_file(), "别的包的文件不受影响")


if __name__ == "__main__":
    unittest.main()
