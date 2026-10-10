"""伴随包：一对包一起装、一起卸，环只从这条边过；基础运行时是唯一例外。

真身是 `bepinex.bepinex-be` 与 `hans21223.sprocket-mod-loader`：补丁往 `bepinex:core` 里落文件，
那条隐含依赖让它必须排在加载器之后；加载器要补丁才能在本机游戏版本上跑，那条需求写在
`companions` 里 —— 写成 `dependencies` 就成了环。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_patch_install import BRIDGE_PATH, LOADER_ID, PATCH_ID, combined_plan, game_dir
from test_patch_install import package as installer_package
from test_solver import FakeGitHub, package as solver_package, release

from sprocket_mod_manager.application.solver import DependencySolver
from sprocket_mod_manager.domain.errors import InstallError, ResolutionError
from sprocket_mod_manager.domain.models import MODLOADER_KIND, PreparedPlan, RegistryPackage
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.infrastructure.installer import Installer
from sprocket_mod_manager.infrastructure.state import StateStore

PATCH_RULE = {"match": "Patch/BepInEx/core/*.dll", "type": "bepinex:core"}


def companion(package_id: str, version: str = "*") -> dict[str, str]:
    return {"id": package_id, "version": version}


def loader_entry(*companions: str):
    """加载器条目：供给 `bepinex:core`，伴随补丁。"""
    return replace(
        solver_package(LOADER_ID),
        kind=MODLOADER_KIND,
        supply={"bepinex:core": "{Sprocket}/BepInEx/core"},
        companions=tuple(companion(item) for item in companions),
    )


def patch_entry(*companions: str):
    """补丁条目：安装规则写下 `bepinex:core`，因此有一条指向加载器的隐含依赖。"""
    return replace(
        solver_package(PATCH_ID),
        install={"mode": "patch", "files": [dict(PATCH_RULE)], "scan_dlls": False, "exclude": []},
        file_rules=(dict(PATCH_RULE),),
        companions=tuple(companion(item) for item in companions),
    )


def with_companions(prepared: PreparedPlan, pairs: dict[str, tuple[str, ...]]) -> PreparedPlan:
    """把伴随关系补进计划：安装记录里 `companions` 一列就是从这里来的。"""
    for item in prepared.packages:
        item.resolved = replace(
            item.resolved, companion_ids=pairs.get(item.resolved.package.id, ())
        )
    return prepared


class CompanionResolutionTests(unittest.TestCase):
    def test_a_pair_resolves_together_with_the_loader_first(self):
        loader = loader_entry(PATCH_ID)
        patch_entry_item = patch_entry(LOADER_ID)
        github = FakeGitHub({LOADER_ID: (release("6.0.0"),), PATCH_ID: (release("1.0.0"),)})

        plan = DependencySolver(Registry([loader, patch_entry_item]), github).resolve(LOADER_ID)

        self.assertEqual([item.package.id for item in plan.packages], [LOADER_ID, PATCH_ID])
        by_id = plan.by_id()
        self.assertEqual(by_id[PATCH_ID].dependency_ids, (LOADER_ID,), "补丁的加载器依赖是隐含的那条")
        self.assertEqual(by_id[LOADER_ID].dependency_ids, (), "伴随边不是依赖")
        self.assertEqual(by_id[LOADER_ID].companion_ids, (PATCH_ID,))
        self.assertEqual(by_id[PATCH_ID].companion_ids, (LOADER_ID,))

    def test_a_companion_version_range_is_enforced(self):
        loader = replace(loader_entry(PATCH_ID), companions=({"id": PATCH_ID, "version": ">=2.0.0"},))
        github = FakeGitHub({LOADER_ID: (release("6.0.0"),), PATCH_ID: (release("1.0.0"),)})

        with self.assertRaisesRegex(ResolutionError, ">=2.0.0"):
            DependencySolver(Registry([loader, patch_entry(LOADER_ID)]), github).resolve(LOADER_ID)

    def test_a_pair_without_hard_edges_installs_together_root_first(self):
        first = replace(solver_package("test.a"), companions=(companion("test.b"),))
        second = replace(solver_package("test.b"), companions=(companion("test.a"),))
        github = FakeGitHub({"test.a": (release("1.0.0"),), "test.b": (release("1.0.0"),)})

        plan = DependencySolver(Registry([first, second]), github).resolve("test.a")

        self.assertEqual([item.package.id for item in plan.packages], ["test.a", "test.b"])

    def test_a_one_sided_declaration_still_pairs_up_both_ways(self):
        """私有来源的条目可能只写一边：客户端按双向闭包算，卸载从哪边发起都一样。"""
        first = replace(solver_package("test.a"), companions=(companion("test.b"),))
        second = solver_package("test.b")
        github = FakeGitHub({"test.a": (release("1.0.0"),), "test.b": (release("1.0.0"),)})

        plan = DependencySolver(Registry([first, second]), github).resolve("test.a")

        self.assertEqual(plan.by_id()["test.a"].companion_ids, ("test.b",))
        self.assertEqual(plan.by_id()["test.b"].companion_ids, ("test.a",))


class CompanionRegistryEntryTests(unittest.TestCase):
    """索引 → 领域模型这一段：伴随关系解析出来，坏形状只让那一条标成不可用。"""

    @staticmethod
    def entry(**overrides) -> dict:
        return {
            "schema_version": 3,
            "id": "example.pair",
            "name": "ExamplePair",
            "authors": ["ExampleAuthor"],
            "repository": "ExampleAuthor/ExamplePair",
            "license": "MIT",
            "dependencies": [],
            "install": {},
            **overrides,
        }

    def test_companions_parse_into_the_model(self):
        package = RegistryPackage.from_dict(
            self.entry(companions=[{"id": "example.other", "version": ">=1.0.0"}])
        )

        self.assertEqual(package.companions, ({"id": "example.other", "version": ">=1.0.0"},))

    def test_a_malformed_companion_only_disables_that_entry(self):
        registry = Registry.from_dict({
            "schema_version": 1,
            "packages": [
                self.entry(),
                self.entry(id="example.bad", name="Bad", companions=[{"id": "example.other"}]),
            ],
        })

        self.assertTrue(registry.has_package("example.pair"))
        bad = next(package for package in registry.packages if package.id == "example.bad")
        self.assertTrue(
            any("companions must name a package and a version" in issue for issue in bad.issues)
        )


class CompanionInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        running = patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
        running.start()
        self.addCleanup(running.stop)

    def install_pair(
            self,
            root: Path,
            game: Path,
            store: StateStore,
            *,
            loader_kind: str = "",
            patch_path: str = BRIDGE_PATH,
    ) -> Installer:
        installer = Installer(root / "app", store)
        loader = installer_package(LOADER_ID, kind=loader_kind)
        patch_package = installer_package(PATCH_ID, mode="patch", dependencies=(LOADER_ID,))
        prepared = combined_plan(
            root,
            [
                (loader, [(BRIDGE_PATH, b"loader bridge")]),
                (patch_package, [(patch_path, b"patch bridge")]),
            ],
            root_id=LOADER_ID,
        )
        installer.apply(
            with_companions(prepared, {LOADER_ID: (PATCH_ID,), PATCH_ID: (LOADER_ID,)}), game
        )
        return installer

    def test_both_sides_are_recorded_and_the_companion_is_not_an_orphan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")

            self.install_pair(root, game, store)

            packages = store.load()["packages"]
            self.assertEqual(sorted(packages), [LOADER_ID, PATCH_ID])
            self.assertEqual(packages[LOADER_ID]["companions"], [PATCH_ID])
            self.assertEqual(packages[PATCH_ID]["companions"], [LOADER_ID])
            self.assertFalse(packages[PATCH_ID]["requested"], "伴随带进来的那个不是点名装的")

    def test_removing_either_side_removes_both(self):
        for target in (PATCH_ID, LOADER_ID):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                game = game_dir(root)
                store = StateStore(root / "app" / "installed.json")
                installer = self.install_pair(root, game, store)

                removed, warnings = installer.remove(target, game)

                self.assertEqual(removed, sorted([LOADER_ID, PATCH_ID]))
                self.assertEqual(warnings, [])
                self.assertEqual(store.load()["packages"], {})

    def test_removing_a_loader_does_not_drag_a_companion_outside_its_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.install_pair(
                root, game, store, loader_kind=MODLOADER_KIND, patch_path="Mods/PatchBridge.dll"
            )

            removed, _warnings = installer.remove(LOADER_ID, game, modloaders=(LOADER_ID,))

            self.assertEqual(removed, [LOADER_ID], "加载器不连坐伴随包")
            self.assertEqual(list(store.load()["packages"]), [PATCH_ID])

    def test_a_companion_is_not_a_dependent_that_blocks_the_loader(self):
        """补丁的隐含依赖落在加载器上：那条依赖不该把加载器的卸载挡死。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.install_pair(root, game, store, loader_kind=MODLOADER_KIND)
            self.assertEqual(
                store.load()["packages"][PATCH_ID]["dependencies"], [LOADER_ID],
                "这条隐含依赖确实记在补丁名下",
            )

            removed, warnings = installer.remove(LOADER_ID, game, modloaders=(LOADER_ID,))

            self.assertEqual(removed, [LOADER_ID])
            self.assertEqual(warnings, [])
            # 补丁的文件落在加载器供给的目录里：卸载加载器时它们随归档一起走，
            # 记录也进那份清单 —— 重装加载器时整棵搬回来。
            manifest = json.loads(
                (
                    game / "SprocketModManager" / "backup" / "loaders" / LOADER_ID
                    / "manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(list(manifest["packages"]), [PATCH_ID])
            self.assertEqual(list(manifest["files"]), [BRIDGE_PATH])

    def test_another_package_requiring_the_companion_blocks_the_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.install_pair(root, game, store)
            prepared = combined_plan(
                root,
                [(installer_package("test.consumer"), [("Mods/Consumer.dll", b"consumer")])],
                root_id="test.consumer",
            )
            for item in prepared.packages:
                item.resolved = replace(item.resolved, dependency_ids=(PATCH_ID,))
            installer.apply(prepared, game)

            with self.assertRaisesRegex(InstallError, f"{PATCH_ID} is required by: test.consumer"):
                installer.remove(LOADER_ID, game)

    def test_reinstalling_the_loader_keeps_the_patch_content_and_its_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.install_pair(root, game, store, loader_kind=MODLOADER_KIND)
            self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"patch bridge")

            # 重装计划只点名加载器：补丁已经装着、版本没变，这次不重铺，但伴随关系照旧落在记录里。
            loader = installer_package(LOADER_ID, kind=MODLOADER_KIND)
            plan = combined_plan(
                root, [(loader, [(BRIDGE_PATH, b"loader bridge")])], root_id=LOADER_ID
            )
            warnings = installer.apply(
                with_companions(plan, {LOADER_ID: (PATCH_ID,)}), game
            )

            self.assertEqual(warnings, [f"preserved {BRIDGE_PATH}: owned by another package"])
            self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"patch bridge", "补丁的内容不被静默顶掉")
            self.assertEqual(list(store.load()["packages"]), [LOADER_ID, PATCH_ID])


if __name__ == "__main__":
    unittest.main()
