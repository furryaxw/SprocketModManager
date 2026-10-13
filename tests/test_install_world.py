"""安装时的世界：装一个包不能把已装包的依赖弄坏，同批一起装的包也要能共存。

真机上的样子是「装一条老线路的模组 → 它要的依赖版本比已装的那些要的低 → 已装的模组被静默
弄坏」。这里用一条两线路的模组（同一条目按自己的版本切换依赖区间）走真实的安装链复现两次：
单独装它、和另一个根同一批装。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_prepare_satisfied import FakeHttp, release_for, zip_bytes  # noqa: E402

from sprocket_mod_manager.domain.compatibility import CapabilityEnvironment  # noqa: E402
from sprocket_mod_manager.domain.errors import InstallError, ResolutionError  # noqa: E402
from sprocket_mod_manager.domain.models import MODFILE_KIND, RegistryPackage  # noqa: E402
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402
from sprocket_mod_manager.application.service import ModManagerService  # noqa: E402
from sprocket_mod_manager.infrastructure.config import ConfigStore  # noqa: E402
from sprocket_mod_manager.infrastructure.manager_paths import state_file_path  # noqa: E402
from sprocket_mod_manager.infrastructure.state import StateStore  # noqa: E402
from sprocket_mod_manager.presentation.web_gui import ClientApi  # noqa: E402
from sprocket_mod_manager.utilities.checksums import sha256_file  # noqa: E402

API = "test.api"
HOST = "test.host"          # 已装：只要 api 1.x
OLD = "test.old"            # 两线路：2.0.0 要 api 0.3.x，1.0.0 不要 api
OTHER = "test.other"        # 与 OLD 同批：要 api 1.x
CONFLICT = "test.conflict"  # 只有一版、无条件要 api 0.3.x：和 HOST 解不到一起

CONTENTS = {
    "api-1.0.0.zip": {"Api.dll": b"api 1.0.0"},
    "api-0.3.0.zip": {"Api.dll": b"api 0.3.0"},
    "host-1.0.0.zip": {"Host.dll": b"host 1.0.0"},
    "old-2.0.0.zip": {"Old.dll": b"old 2.0.0"},
    "old-1.0.0.zip": {"Old.dll": b"old 1.0.0"},
    "other-1.0.0.zip": {"Other.dll": b"other 1.0.0"},
    "conflict-1.0.0.zip": {"Conflict.dll": b"conflict 1.0.0"},
}
ARCHIVES = {name: zip_bytes(entries) for name, entries in CONTENTS.items()}


def package(package_id: str, dependencies: tuple[dict[str, str], ...] = ()) -> RegistryPackage:
    return RegistryPackage(
        id=package_id,
        name=package_id,
        authors=("test",),
        repository=f"test/{package_id}",
        license="MIT",
        display_name={"en": package_id},
        description={"en": "test mod"},
        release={"assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=dependencies,
        install={"overrides": [{"match": "**", "target": "Mods"}]},
        category="utility",
        tags=(),
        kind=MODFILE_KIND,
    )


def dependency(version_range: str, when: str = "*") -> dict[str, str]:
    return {"id": API, "version": version_range, "when": when}


class ReleasingGitHub:
    def __init__(self, releases):
        self._releases = releases

    def releases(self, package: RegistryPackage):
        return self._releases[package.id]

    @staticmethod
    def install_assets(_package: RegistryPackage, release):
        return release.assets


class WorldFixture(unittest.TestCase):
    """一套真机形状的假注册表 + 假游戏目录：安装链走的是真实代码。"""

    def setUp(self) -> None:
        running = patch(
            "sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False
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
        self.service.registry = Registry([
            package(API),
            package(HOST, (dependency(">=1.0.0 <2.0.0"),)),
            package(OLD, (dependency(">=0.3.0 <1.0.0", ">=2.0.0"),)),
            package(OTHER, (dependency(">=1.0.0 <2.0.0"),)),
            package(CONFLICT, (dependency(">=0.3.0 <1.0.0"),)),
        ])
        self.http = FakeHttp(dict(ARCHIVES))
        self.service.http = self.http
        self.service.github = ReleasingGitHub({
            API: (release_for(API, "1.0.0", "api-1.0.0.zip"), release_for(API, "0.3.0", "api-0.3.0.zip")),
            HOST: (release_for(HOST, "1.0.0", "host-1.0.0.zip"),),
            OLD: (release_for(OLD, "2.0.0", "old-2.0.0.zip"), release_for(OLD, "1.0.0", "old-1.0.0.zip")),
            OTHER: (release_for(OTHER, "1.0.0", "other-1.0.0.zip"),),
            CONFLICT: (release_for(CONFLICT, "1.0.0", "conflict-1.0.0.zip"),),
        })
        self._api = ClientApi(
            "test", app_dir=self.app_dir, service_factory=lambda _app_dir: self.service
        )
        self.addCleanup(self._api.install_queue.close)

    def _seed_installed(self, records: dict[str, tuple[str, tuple[str, ...], bool]]) -> None:
        """把「已经装着」写进安装记录：每个包的载荷按它在发布包里的内容落盘。"""
        packages: dict[str, dict] = {}
        files: dict[str, dict] = {}
        for package_id, (version, dependencies, requested) in records.items():
            asset = {
                API: f"api-{version}.zip",
                HOST: f"host-{version}.zip",
                OLD: f"old-{version}.zip",
                OTHER: f"other-{version}.zip",
            }[package_id]
            landed: list[str] = []
            for name, content in CONTENTS[asset].items():
                relative = f"Mods/{name}"
                path = self.game / relative
                path.write_bytes(content)
                files[relative] = {
                    "sha256": sha256_file(path), "owners": [package_id], "disabled": False,
                }
                landed.append(relative)
            packages[package_id] = {
                "id": package_id,
                "name": package_id,
                "repository": f"test/{package_id}",
                "version": version,
                "requested": requested,
                "dependencies": list(dependencies),
                "files": landed,
            }
        StateStore(state_file_path(self.game)).save({
            "schema_version": 2, "packages": packages, "files": files, "metadata": {},
        })

    def _packages(self) -> dict[str, dict]:
        return StateStore(state_file_path(self.game)).load()["packages"]

    def _run_queue(self) -> list[str]:
        self.assertTrue(self._api.install_queue.wait_until_idle(5), "队列没有跑完")
        return [entry.state for entry in self._api.install_queue.snapshot()]


class InstallWorldTests(WorldFixture):
    def test_the_named_root_gives_way_instead_of_downgrading_an_installed_dependency(self) -> None:
        self._seed_installed({HOST: ("1.0.0", (API,), True), API: ("1.0.0", (), False)})

        plan, _warnings = self.service.install(OLD, self.game)

        self.assertEqual(str(plan.by_id()[OLD].release.version), "1.0.0")
        packages = self._packages()
        self.assertEqual(packages[API]["version"], "1.0.0", "已装的 api 不该被降级")
        self.assertEqual(packages[HOST]["version"], "1.0.0")
        self.assertEqual(packages[OLD]["version"], "1.0.0")
        self.assertNotIn("api-0.3.0.zip", self.http.downloaded)
        self.assertEqual((self.game / "Mods" / "Old.dll").read_bytes(), b"old 1.0.0")

    def test_a_batch_lands_on_versions_that_can_coexist(self) -> None:
        result = self._api.enqueue_install([OTHER, OLD])

        self.assertTrue(result["ok"], result)
        self.assertEqual(self._run_queue(), ["completed", "completed"])
        packages = self._packages()
        self.assertEqual(packages[OTHER]["version"], "1.0.0")
        self.assertEqual(packages[API]["version"], "1.0.0")
        self.assertEqual(packages[OLD]["version"], "1.0.0", "同批里让路的是点名的那个包")
        self.assertEqual((self.game / "Mods" / "Api.dll").read_bytes(), b"api 1.0.0")


class ConfirmedRowTests(WorldFixture):
    """执行一行已经确认过的计划：按那一版落盘，世界只作偏好，缺口的依赖不再被拉回来。"""

    def test_removing_together_is_not_blocked_by_each_other(self) -> None:
        self._seed_installed({HOST: ("1.0.0", (API,), True), API: ("1.0.0", (), False)})

        with self.assertRaises(InstallError) as refused:
            self.service.remove(API, self.game)
        self.assertIn("required by", str(refused.exception))

        removed, _warnings = self.service.remove_many([API, HOST], self.game)

        self.assertEqual(sorted(removed), [API, HOST])
        self.assertEqual(self._packages(), {}, "两个都走了：没有再被对方挡住")

    def test_execution_does_not_refuse_what_the_plan_approved(self) -> None:
        self._seed_installed({HOST: ("1.0.0", (API,), True), API: ("1.0.0", (), False)})

        with self.assertRaises(ResolutionError):
            self.service.install(OLD, self.game, version_range="=2.0.0")

        plan, _warnings = self.service.install(
            OLD,
            self.game,
            version_range="=2.0.0",
            constrain_world=False,
            ignore_edges={(OLD, API)},
        )

        self.assertEqual(str(plan.by_id()[OLD].release.version), "2.0.0")
        packages = self._packages()
        self.assertEqual(packages[OLD]["version"], "2.0.0")
        self.assertEqual(packages[API]["version"], "1.0.0", "计划里留着缺口的依赖不被拉回来")
        self.assertEqual(packages[HOST]["version"], "1.0.0")
        self.assertEqual((self.game / "Mods" / "Old.dll").read_bytes(), b"old 2.0.0")
        self.assertNotIn("api-0.3.0.zip", self.http.downloaded)


class ChangeApiTests(WorldFixture):
    """计划页那两个入口：只传选中的 id 与选择，后端解出计划并按它执行。"""

    def test_the_plan_lists_what_would_be_installed(self) -> None:
        result = self._api.plan_changes([OTHER, OLD])

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            result["install"],
            {OTHER: "1.0.0", OLD: "1.0.0", API: "1.0.0"},
            "会装的那些也带上依赖",
        )
        self.assertEqual(result["remove"], [])
        self.assertEqual(
            {line["id"]: line["action"] for line in result["lines"]},
            {OTHER: "install", OLD: "install", API: "install"},
            "点名的两个加上被带进来的那个依赖，各占一行",
        )
        self.assertEqual(
            [line["id"] for line in result["lines"] if line["named"]],
            [OTHER, OLD],
            "只有点名的那些行到了执行那一步",
        )

    def test_a_conflict_is_a_plan_not_a_refusal(self) -> None:
        result = self._api.plan_changes([HOST, CONFLICT])

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["install"], {})
        self.assertEqual([line["action"] for line in result["lines"]], ["skip", "skip"])
        self.assertEqual([line["problems"][0]["kind"] for line in result["lines"]], ["conflict"] * 2)

    def test_applying_the_plan_installs_the_planned_versions(self) -> None:
        planned = self._api.plan_changes([OTHER, OLD])

        applied = self._api.apply_changes([OTHER, OLD])

        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["count"], 2)
        self.assertEqual(self._run_queue(), ["completed", "completed"])
        packages = self._packages()
        self.assertEqual(packages[OTHER]["version"], planned["install"][OTHER])
        self.assertEqual(packages[OLD]["version"], planned["install"][OLD])
        self.assertEqual(packages[API]["version"], "1.0.0")

    def test_applying_a_removal_goes_through_the_plan_and_not_the_queue(self) -> None:
        self._seed_installed({HOST: ("1.0.0", (API,), True), API: ("1.0.0", (), False)})

        result = self._api.apply_changes([HOST], {"test.host": "remove"})

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 0, "卸载不进下载队列")
        self.assertEqual(self._api.install_queue.snapshot(), ())
        self.assertEqual(sorted(result["removed"]), [API, HOST])
        self.assertEqual(self._packages(), {})


if __name__ == "__main__":
    unittest.main()
