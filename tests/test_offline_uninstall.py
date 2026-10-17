"""卸载只读安装记录：注册表里没有这个包、甚至目录根本没加载，都拦不住它。

真机上的形状是「服务器掉线后，装过的包还在已安装页，点卸载却报未注册」。这里用公开包的同一条路
钉住它：记录里在就卸得掉，注册表只负责接住本机没装、按名字点名的调用。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.application.service import ModManagerService
from sprocket_mod_manager.domain.compatibility import CapabilityEnvironment
from sprocket_mod_manager.domain.errors import InstallError
from sprocket_mod_manager.domain.models import MODFILE_KIND, RegistryPackage
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.infrastructure.config import ConfigStore
from sprocket_mod_manager.infrastructure.manager_paths import state_file_path
from sprocket_mod_manager.infrastructure.state import StateStore
from sprocket_mod_manager.presentation.web_gui import ClientApi
from sprocket_mod_manager.utilities.checksums import sha256_file

MOD = "test.installed-mod"
MOD_NAME = "Installed Mod"
PAYLOAD = "Mods/Installed.dll"
LOADER = "test.recorded-melonloader"


def package(package_id: str) -> RegistryPackage:
    return RegistryPackage(
        id=package_id,
        name="Installed Mod",
        authors=("test",),
        repository="test/installed-mod",
        license="MIT",
        display_name={"en": "Installed Mod"},
        description={"en": "test mod"},
        release={"assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=(),
        install={"files": [{"match": "*.dll", "type": "melonloader:mod"}]},
        category="utility",
        tags=(),
        kind=MODFILE_KIND,
    )


class OfflineUninstallTests(unittest.TestCase):
    def setUp(self) -> None:
        running = patch(
            "sprocket_mod_manager.infrastructure.installer.sprocket_is_running",
            return_value=False,
        )
        running.start()
        self.addCleanup(running.stop)
        environment = patch.object(
            ClientApi,
            "current_environment",
            return_value=CapabilityEnvironment(sprocket="0.2.53.2", sprocket_state="ok"),
        )
        environment.start()
        self.addCleanup(environment.stop)
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        root = Path(self._temporary.name)
        self.game = root / "game"
        (self.game / "Mods").mkdir(parents=True)
        (self.game / "Sprocket.exe").touch()
        self.app_dir = root / "app"
        ConfigStore(self.app_dir).save({
            "language": "zh", "game_path": str(self.game), "index_url": "",
        })
        self.service = ModManagerService(self.app_dir)
        # 注册表里没有这个包：私有服务器掉线、条目下架，都是这个形状。
        self.service.registry = Registry([])
        self._api = ClientApi(
            "test", app_dir=self.app_dir, service_factory=lambda _app_dir: self.service
        )
        self.addCleanup(self._api.install_queue.close)

    def _seed_installed(self) -> None:
        """把「已经装着」写进安装记录：载荷落盘，记录里的 `files` 指向它。"""
        path = self.game / PAYLOAD
        path.write_bytes(b"installed payload")
        StateStore(state_file_path(self.game)).save({
            "schema_version": 2,
            "packages": {
                MOD: {
                    "id": MOD,
                    "name": MOD_NAME,
                    "repository": "test/installed-mod",
                    "version": "1.0.0",
                    "requested": True,
                    "dependencies": [],
                    "files": [PAYLOAD],
                }
            },
            "files": {
                PAYLOAD: {
                    "sha256": sha256_file(path), "owners": [MOD], "disabled": False,
                }
            },
            "metadata": {},
        })

    def _packages(self) -> dict[str, dict]:
        return StateStore(state_file_path(self.game)).load()["packages"]

    def _seed_installed_loader(self) -> None:
        """装着一个加载器：记录带 `kind`，磁盘上留着它的那棵树。"""
        (self.game / "MelonLoader" / "net6").mkdir(parents=True)
        (self.game / "MelonLoader" / "net6" / "MelonLoader.dll").write_bytes(b"loader")
        StateStore(state_file_path(self.game)).save({
            "schema_version": 2,
            "packages": {
                LOADER: {
                    "id": LOADER,
                    "name": "MelonLoader",
                    "repository": "LavaGang/MelonLoader",
                    "version": "0.7.3",
                    "requested": True,
                    "kind": "modloader",
                    "files": [],
                    "directories": ["MelonLoader"],
                    "payload_files": [],
                }
            },
            "files": {},
            "metadata": {},
        })

    def test_a_package_the_registry_does_not_know_still_uninstalls(self) -> None:
        self._seed_installed()

        removed, warnings = self.service.remove(MOD, self.game)

        self.assertEqual(removed, [MOD])
        self.assertEqual(warnings, [])
        self.assertFalse((self.game / PAYLOAD).exists(), "载荷要跟着走")
        self.assertEqual(self._packages(), {})

    def test_an_install_record_name_resolves_without_the_registry(self) -> None:
        self._seed_installed()

        removed, _warnings = self.service.remove(MOD_NAME, self.game)

        self.assertEqual(removed, [MOD], "记录里的 name 也要认")

    def test_the_plan_marks_an_unregistered_installed_package_for_removal(self) -> None:
        self._seed_installed()

        result = self._api.plan_changes([MOD], {MOD: "remove"})

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["remove"], [MOD])
        line = result["lines"][0]
        self.assertEqual(line["action"], "remove")
        self.assertEqual(line["problems"], [], "装着的包不该因为没有注册表条目就标红")
        self.assertEqual(line["name"], MOD_NAME, "名字从记录来")

    def test_a_removal_only_request_needs_no_registry_at_all(self) -> None:
        self._seed_installed()
        self.service.registry = None

        planned = self._api.plan_changes([MOD], {MOD: "remove"})
        self.assertTrue(planned["ok"], planned)
        applied = self._api.apply_changes([MOD], {MOD: "remove"})

        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["removed"], [MOD])
        self.assertFalse((self.game / PAYLOAD).exists())

    def test_an_id_that_is_not_installed_is_refused(self) -> None:
        self._seed_installed()

        with self.assertRaises(InstallError) as refused:
            self.service.remove("test.never-installed", self.game)

        self.assertIn("not installed", str(refused.exception))
        self.assertTrue((self.game / PAYLOAD).is_file(), "别的包一个都不许动")

    def test_removing_something_that_is_not_installed_is_no_removal(self) -> None:
        self._seed_installed()

        result = self._api.plan_changes(["test.never-installed"], {"test.never-installed": "remove"})

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["remove"], [], "没装的东西不进卸载闭包")

    def test_a_recorded_modloader_uninstalls_without_the_registry(self) -> None:
        self._seed_installed_loader()
        self.service.registry = None

        result = self._api.remove_modloader(LOADER)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["removed"], [LOADER])
        self.assertFalse((self.game / "MelonLoader").exists(), "加载器那棵树跟着走")
        self.assertEqual(self._packages(), {})

    def test_something_that_is_not_a_modloader_is_refused(self) -> None:
        self._seed_installed()

        result = self._api.remove_modloader(MOD)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "modloader_remove_failed")
        self.assertTrue((self.game / PAYLOAD).is_file(), "一个都不许动")
        self.assertIn(MOD, self._packages())


if __name__ == "__main__":
    unittest.main()
