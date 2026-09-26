"""重装：显式要求把文件覆盖回去，不因为「解析出来的版本就是装着的那版」被跳过。

同版本重装是「重装」的正常形态（修一份被改动过的文件、把被删的文件装回来），所以入队要有一条
明确的通路；没有这条通路时队列返回 `count: 0`，什么也不做。重装不走卸载，因此被别的包依赖
也拦不住。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_prepare_satisfied import FakeHttp, ReleasingGitHub, release_for, zip_bytes  # noqa: E402

from sprocket_mod_manager.domain.compatibility import CapabilityEnvironment  # noqa: E402
from sprocket_mod_manager.domain.errors import InstallError  # noqa: E402
from sprocket_mod_manager.domain.models import MODFILE_KIND, RegistryPackage  # noqa: E402
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402
from sprocket_mod_manager.application.service import ModManagerService  # noqa: E402
from sprocket_mod_manager.infrastructure.config import ConfigStore  # noqa: E402
from sprocket_mod_manager.infrastructure.manager_paths import state_file_path  # noqa: E402
from sprocket_mod_manager.infrastructure.state import StateStore  # noqa: E402
from sprocket_mod_manager.presentation.web_gui import ClientApi  # noqa: E402
from sprocket_mod_manager.utilities.checksums import sha256_file  # noqa: E402

MOD = "test.mod"
DEP = "test.dep"
MOD_PATH = "Mods/Mod.dll"
DEP_PATH = "Mods/Dep.dll"
SHIPPED = b"shipped"


def package(package_id: str, *, dependencies: tuple[str, ...] = ()) -> RegistryPackage:
    """一个落在 `Mods` 下的普通模组（v1 override，不牵扯加载器供给表）。"""
    return RegistryPackage(
        id=package_id,
        name=package_id,
        authors=("test",),
        repository=f"test/{package_id}",
        license="MIT",
        display_name={"en": package_id},
        description={"en": "test mod"},
        release={"assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=tuple({"id": item, "version": "*", "when": "*"} for item in dependencies),
        install={"scan_dlls": False, "exclude": [], "overrides": [{"match": "**", "target": "Mods"}]},
        category="utility",
        tags=(),
        kind=MODFILE_KIND,
    )


class ReinstallTests(unittest.TestCase):
    def setUp(self) -> None:
        running = patch(
            "sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False
        )
        running.start()
        self.addCleanup(running.stop)
        # 本机游戏版本读得出来：依赖才会被环境判定放过（读不出来时求解器会把依赖全筛掉）。
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
        self.service.registry = Registry([package(MOD, dependencies=(DEP,)), package(DEP)])
        self.http = FakeHttp({
            "mod.zip": zip_bytes({"Mod.dll": SHIPPED}),
            "dep.zip": zip_bytes({"Dep.dll": b"dependency"}),
        })
        self.service.http = self.http
        self.service.github = ReleasingGitHub({
            MOD: release_for(MOD, "1.0.0", "mod.zip"),
            DEP: release_for(DEP, "1.0.0", "dep.zip"),
        })
        self.mod_file = self.game / "Mods" / "Mod.dll"
        self.dep_file = self.game / "Mods" / "Dep.dll"
        self._seed_installed()
        self._api = ClientApi(
            "test", app_dir=self.app_dir, service_factory=lambda _app_dir: self.service
        )
        self.addCleanup(self._api.install_queue.close)

    def _seed_installed(self) -> None:
        """把「已经装着」写进安装记录：文件按给的内容落盘，摘要取当下的内容。"""
        records = {
            MOD: ({MOD_PATH: b"stale but recorded"}, (DEP,)),
            DEP: ({DEP_PATH: b"old dependency"}, ()),
        }
        packages: dict[str, dict] = {}
        files: dict[str, dict] = {}
        for owner, (contents, dependencies) in records.items():
            for relative, content in contents.items():
                path = self.game / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                files[relative] = {
                    "sha256": sha256_file(path), "owners": [owner], "disabled": False,
                }
            packages[owner] = {
                "id": owner,
                "name": owner,
                "repository": f"test/{owner}",
                "version": "1.0.0",
                "requested": True,
                "dependencies": list(dependencies),
                "files": list(contents),
            }
        StateStore(state_file_path(self.game)).save({
            "schema_version": 2, "packages": packages, "files": files, "metadata": {},
        })

    def _run_queue(self) -> list[str]:
        self.assertTrue(self._api.install_queue.wait_until_idle(5), "队列没有跑完")
        return [entry.state for entry in self._api.install_queue.snapshot()]

    def test_a_plain_request_at_the_installed_version_is_still_skipped(self) -> None:
        """没说要重装时，「已经装了这一版」照旧不入队。"""
        result = self._api.enqueue_install([MOD], versions={MOD: "1.0.0"})

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 0)
        self.assertEqual(self._api.install_queue.snapshot(), ())

    def test_an_explicit_reinstall_at_the_installed_version_is_queued(self) -> None:
        """显式重装：同版本也排队，跑完之后文件是发布包里那份。"""
        result = self._api.enqueue_install([MOD], versions={MOD: "1.0.0"}, reinstall=True)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 1)
        self.assertEqual(self._run_queue(), ["completed"])
        self.assertEqual(self.mod_file.read_bytes(), SHIPPED)

    def test_a_forced_retry_at_the_installed_version_is_queued(self) -> None:
        """「强制重试」也走同一条通路：它已经在队列里，不该再被版本门挡一次。"""
        self.mod_file.write_bytes(b"hand edited")

        result = self._api.enqueue_install([MOD], True, {MOD: "1.0.0"})

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 1)
        self.assertEqual(self._run_queue(), ["completed"])
        self.assertEqual(self.mod_file.read_bytes(), SHIPPED)

    def test_reinstalling_a_package_others_depend_on_is_not_refused(self) -> None:
        """重装不走卸载：被别的已装包依赖的包照样能重装。"""
        with self.assertRaises(InstallError) as refused:
            self.service.remove(DEP, self.game)
        self.assertIn("required by", str(refused.exception), "这份记录确实带着依赖关系")

        result = self._api.enqueue_install([DEP], versions={DEP: "1.0.0"}, reinstall=True)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 1, "重装不该被「有别的包依赖它」拦下")
        self.assertEqual(self._run_queue(), ["completed"])


if __name__ == "__main__":
    unittest.main()
