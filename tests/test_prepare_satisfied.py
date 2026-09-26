"""准备阶段跳过已经装着那个**同一版本**的依赖：不取回、不重铺。

计划本身不变：目录表由完整的 `ResolutionPlan` 算出，所以被跳过的加载器仍然决定模组的落点，
依赖图也照旧。根包永远准备 —— 点它就是要求重装。
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.application.preparer import PlanPreparer  # noqa: E402
from sprocket_mod_manager.application.service import ModManagerService  # noqa: E402
from sprocket_mod_manager.domain.models import (  # noqa: E402
    MODFILE_KIND,
    MODLOADER_KIND,
    RegistryPackage,
    ReleaseAsset,
    ReleaseInfo,
    ResolvedPackage,
    ResolutionPlan,
)
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402
from sprocket_mod_manager.domain.semver import Version  # noqa: E402
from sprocket_mod_manager.infrastructure.manager_paths import state_file_path  # noqa: E402
from sprocket_mod_manager.infrastructure.state import StateStore  # noqa: E402

LOADER = "test.loader"
MOD = "test.mod"
MOD_TYPE = "test:mod"


def loader_package() -> RegistryPackage:
    return RegistryPackage(
        id=LOADER,
        name="Loader",
        authors=("test",),
        repository="test/loader",
        license="MIT",
        display_name={"en": "Loader"},
        description={"en": "test loader"},
        release={"assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=(),
        install={"payload": [{"match": "**", "target": "{Sprocket}", "layout": "tree"}]},
        category="utility",
        tags=(),
        kind=MODLOADER_KIND,
        supply={MOD_TYPE: "{Sprocket}/Mods"},
        payload_rules=({"match": "**", "target": "{Sprocket}", "layout": "tree"},),
    )


def mod_package() -> RegistryPackage:
    rule = {"match": "**", "type": MOD_TYPE, "layout": "tree"}
    return RegistryPackage(
        id=MOD,
        name="Mod",
        authors=("test",),
        repository="test/mod",
        license="MIT",
        display_name={"en": "Mod"},
        description={"en": "test mod"},
        release={"assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=({"id": LOADER, "version": "*", "when": "*"},),
        install={"files": [rule], "scan_dlls": False, "exclude": []},
        category="utility",
        tags=(),
        kind=MODFILE_KIND,
        schema_version=2,
        file_rules=(rule,),
    )


def release_for(package_id: str, version: str, asset_name: str) -> ReleaseInfo:
    asset = ReleaseAsset(
        1,
        asset_name,
        1,
        f"https://github.com/test/repo/releases/download/v{version}/{asset_name}",
    )
    return ReleaseInfo(1, f"v{version}", Version.parse(version), False, "", (asset,))


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        for name, content in entries.items():
            output.writestr(name, content)
    return buffer.getvalue()


class FakeGitHub:
    @staticmethod
    def install_assets(_package: RegistryPackage, release: ReleaseInfo) -> tuple[ReleaseAsset, ...]:
        return release.assets


class ReleasingGitHub(FakeGitHub):
    def __init__(self, releases: dict[str, ReleaseInfo]):
        self._releases = releases

    def releases(self, package: RegistryPackage) -> tuple[ReleaseInfo, ...]:
        return (self._releases[package.id],)


class FakeHttp:
    """只实现 `prepare` 用到的取回：把资产名写成一份真的 ZIP。"""

    def __init__(self, archives: dict[str, bytes]):
        self._archives = archives
        self.downloaded: list[str] = []

    def download(self, asset: ReleaseAsset, destination: Path, *, progress=None, hosts=None) -> Path:
        self.downloaded.append(asset.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._archives[asset.name])
        return destination


class PrepareSatisfiedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        loader_release = release_for(LOADER, "1.0.0", "loader.zip")
        mod_release = release_for(MOD, "2.0.0", "mod.zip")
        self.plan = ResolutionPlan(
            MOD,
            (
                ResolvedPackage(loader_package(), loader_release, ()),
                ResolvedPackage(mod_package(), mod_release, (LOADER,)),
            ),
        )
        self.http = FakeHttp({
            "loader.zip": zip_bytes({"BepInEx/core/Loader.dll": b"loader"}),
            "mod.zip": zip_bytes({"Mod.dll": b"mod"}),
        })
        self.preparer = PlanPreparer(self.root / "app", self.http, FakeGitHub())

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _prepare(self, satisfied: dict[str, str] | None = None):
        prepared = self.preparer.prepare(self.plan, satisfied=satisfied)
        self.addCleanup(PlanPreparer.discard, prepared)
        return prepared

    def test_a_dependency_at_the_same_version_is_not_fetched(self) -> None:
        prepared = self._prepare({LOADER: "1.0.0"})

        self.assertEqual([item.resolved.package.id for item in prepared.packages], [MOD])
        self.assertEqual(self.http.downloaded, ["mod.zip"])
        self.assertEqual(
            [item.target for item in prepared.packages[0].files], ["Mods/Mod.dll"],
            "被跳过的加载器仍要决定模组的落点",
        )

    def test_the_plan_keeps_the_dependency_that_is_already_installed(self) -> None:
        prepared = self._prepare({LOADER: "1.0.0"})

        self.assertEqual(
            [item.package.id for item in prepared.resolution.packages], [LOADER, MOD],
            "依赖图与目录表照旧：跳过的只是取回与落盘",
        )
        self.assertEqual(
            prepared.install_directories, {MOD_TYPE: PurePosixPath("Mods")}
        )

    def test_a_dependency_at_another_version_is_still_fetched(self) -> None:
        prepared = self._prepare({LOADER: "0.9.0"})

        self.assertEqual(
            [item.resolved.package.id for item in prepared.packages], [LOADER, MOD]
        )
        self.assertEqual(self.http.downloaded, ["loader.zip", "mod.zip"])

    def test_a_dependency_without_a_recorded_version_is_still_fetched(self) -> None:
        prepared = self._prepare({})

        self.assertEqual(
            [item.resolved.package.id for item in prepared.packages], [LOADER, MOD]
        )

    def test_the_root_is_prepared_even_when_that_version_is_installed(self) -> None:
        prepared = self._prepare({MOD: "2.0.0"})

        self.assertIn(MOD, [item.resolved.package.id for item in prepared.packages])


class ServiceInstallSkipsSatisfiedTests(unittest.TestCase):
    """端到端：装一个"依赖已经装着"的模组，只取回这个模组自己的载荷。"""

    def test_installing_a_mod_does_not_fetch_the_installed_dependency_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            (game / "Mods").mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            store = StateStore(state_file_path(game))
            store.save({
                "schema_version": 2,
                "packages": {
                    LOADER: {
                        "id": LOADER,
                        "name": "Loader",
                        "repository": "test/loader",
                        "version": "1.0.0",
                        "requested": True,
                        "kind": MODLOADER_KIND,
                        "dependencies": [],
                        "files": [],
                        "directories": [],
                        "payload_files": [],
                    }
                },
                "files": {},
                "metadata": {},
            })
            service = ModManagerService(root / "app")
            service.registry = Registry([loader_package(), mod_package()])
            http = FakeHttp({"mod.zip": zip_bytes({"Mod.dll": b"mod"})})
            service.http = http
            service.github = ReleasingGitHub({
                LOADER: release_for(LOADER, "1.0.0", "loader.zip"),
                MOD: release_for(MOD, "2.0.0", "mod.zip"),
            })

            with patch(
                "sprocket_mod_manager.infrastructure.installer.sprocket_is_running",
                return_value=False,
            ):
                plan, warnings = service.install(MOD, game)

            self.assertEqual(warnings, [])
            self.assertEqual(
                [item.package.id for item in plan.packages], [LOADER, MOD],
                "计划照旧含依赖：它供给模组的落点",
            )
            self.assertEqual(http.downloaded, ["mod.zip"], "已装的依赖不再取回一遍")
            self.assertEqual((game / "Mods" / "Mod.dll").read_bytes(), b"mod")
            state = store.load()
            self.assertEqual(state["packages"][LOADER]["version"], "1.0.0")
            self.assertEqual(
                state["files"]["Mods/Mod.dll"]["owners"], [MOD],
            )


if __name__ == "__main__":
    unittest.main()
