"""游戏自己供给的位置：`{Sprocket}` 内与 `{Documents}` 下都装得进、卸得掉，且不产生依赖边。

游戏不是可安装的包，所以它的供给只从索引的 `game.supply` 来（注册表根目录的 `game.json`）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_patch_install import combined_plan, game_dir
from test_patch_install import package as installer_package
from test_solver import FakeGitHub, package as solver_package, release

from sprocket_mod_manager.application.preparer import GAME_SUPPLY_OWNER, PlanPreparer
from sprocket_mod_manager.application.solver import DependencySolver
from sprocket_mod_manager.domain.errors import InstallError
from sprocket_mod_manager.domain.models import ResolvedPackage, ResolutionPlan
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.infrastructure.installer import Installer
from sprocket_mod_manager.infrastructure.known_folders import documents_directory
from sprocket_mod_manager.infrastructure.state import StateStore

GAME_SUPPLY = {
    "sprocket:root": "{Sprocket}",
    "sprocket:userdata": "{Documents}/My Games/Sprocket",
    "sprocket:blueprints": "{Documents}/My Games/Sprocket/Factions/Default/Blueprints",
    "sprocket:streamingassets": "{Sprocket}/Sprocket_Data/StreamingAssets",
}
MOD = "example.mod"
TARGET = "{Documents}/My Games/Sprocket/Mod/config.json"


def entry(package_id: str, **overrides) -> dict:
    return {
        "id": package_id,
        "name": package_id.rsplit(".", 1)[-1],
        "install": {},
        **overrides,
    }


def index(packages: list[dict], game_supply: dict[str, str] | None = None) -> dict:
    return {
        "schema_version": 1,
        "game": {
            "id": "hamish.sprocket",
            "name": "Sprocket",
            "supply": dict(GAME_SUPPLY if game_supply is None else game_supply),
        },
        "packages": packages,
    }


class GameSupplyRegistryTests(unittest.TestCase):
    def test_the_index_carries_the_game_supply(self):
        registry = Registry.from_dict(
            index([entry(MOD, install={"files": [{"match": "**", "type": "sprocket:userdata"}]})])
        )

        self.assertEqual(
            registry.game_supply["sprocket:userdata"], "{Documents}/My Games/Sprocket"
        )
        self.assertEqual(registry.get(MOD).issues, (), "游戏供给的类型算有着落")
        directories = registry.install_directories()
        self.assertEqual(
            directories["sprocket:userdata"].as_posix(), "{Documents}/My Games/Sprocket"
        )
        self.assertEqual(directories["sprocket:root"].as_posix(), ".")

    def test_a_rule_needs_a_supplier_without_the_game_supply(self):
        registry = Registry.from_dict(
            index(
                [entry(MOD, install={"files": [{"match": "**", "type": "sprocket:userdata"}]})],
                game_supply={},
            )
        )

        broken = next(package for package in registry.packages if package.id == MOD)
        self.assertTrue(
            any("not supplied" in issue for issue in broken.issues), broken.issues
        )
        self.assertFalse(registry.has_package(MOD), "没有供给者的规则让它装不了")

    def test_a_game_supplied_type_adds_no_dependency_edge(self):
        """游戏装不了，也不进依赖图：计划里只有那个模组自己。"""
        mod = replace(
            solver_package(MOD),
            install={"files": [{"match": "**", "type": "sprocket:userdata"}]},
            file_rules=({"match": "**", "type": "sprocket:userdata"},),
        )
        registry = Registry([mod], game_supply=GAME_SUPPLY)
        github = FakeGitHub({MOD: (release("1.0.0"),)})

        plan = DependencySolver(registry, github).resolve(MOD)

        self.assertEqual([item.package.id for item in plan.packages], [MOD])


class GameSupplyPlanTests(unittest.TestCase):
    def test_the_install_directory_table_carries_the_game_supply(self):
        mod = installer_package(MOD)
        plan = ResolutionPlan(MOD, (ResolvedPackage(mod, release("1.0.0"), ()),))

        directories = PlanPreparer.install_directories(plan, GAME_SUPPLY)

        self.assertEqual(
            directories["sprocket:blueprints"].as_posix(),
            "{Documents}/My Games/Sprocket/Factions/Default/Blueprints",
        )
        self.assertEqual(directories["sprocket:root"].as_posix(), ".")

    def test_a_package_may_not_supply_a_game_type_in_the_same_plan(self):
        mod = replace(
            installer_package(MOD),
            supply={"sprocket:root": "{Sprocket}/Elsewhere"},
        )
        plan = ResolutionPlan(MOD, (ResolvedPackage(mod, release("1.0.0"), ()),))

        with self.assertRaisesRegex(InstallError, GAME_SUPPLY_OWNER):
            PlanPreparer.install_directories(plan, GAME_SUPPLY)


class DocumentsInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        running = patch(
            "sprocket_mod_manager.infrastructure.installer.sprocket_is_running",
            return_value=False,
        )
        running.start()
        self.addCleanup(running.stop)

    def apply(self, installer: Installer, prepared, game: Path, documents: Path):
        with patch(
            "sprocket_mod_manager.infrastructure.installer.documents_directory",
            return_value=documents,
        ):
            return installer.apply(prepared, game)

    def remove(self, installer: Installer, game: Path, documents: Path):
        with patch(
            "sprocket_mod_manager.infrastructure.installer.documents_directory",
            return_value=documents,
        ):
            return installer.remove(MOD, game)

    def test_a_documents_file_installs_records_and_uninstalls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            documents = root / "Documents"
            documents.mkdir()
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            prepared = combined_plan(
                root, [(installer_package(MOD), [(TARGET, b'{"value": 1}')])], root_id=MOD
            )

            self.apply(installer, prepared, game, documents)

            landed = documents / "My Games" / "Sprocket" / "Mod" / "config.json"
            self.assertEqual(landed.read_bytes(), b'{"value": 1}')
            state = store.load()
            self.assertEqual(list(state["files"]), [TARGET])
            self.assertEqual(
                state["packages"][MOD]["directories"],
                [
                    "{Documents}/My Games",
                    "{Documents}/My Games/Sprocket",
                    "{Documents}/My Games/Sprocket/Mod",
                ],
            )

            removed, warnings = self.remove(installer, game, documents)

            self.assertEqual(removed, [MOD])
            self.assertEqual(warnings, [])
            self.assertFalse(landed.exists())
            self.assertFalse((documents / "My Games").exists(), "装出来的空目录跟着清掉")
            self.assertEqual(store.load()["packages"], {})

    def test_a_record_path_cannot_escape_its_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            prepared = combined_plan(
                root,
                [(installer_package(MOD), [("{Documents}/../Outside.json", b"x")])],
                root_id=MOD,
            )

            with self.assertRaisesRegex(Exception, "unsafe package path"):
                self.apply(installer, prepared, game, root / "Documents")


class KnownFolderTests(unittest.TestCase):
    def test_documents_directory_is_absolute_and_resolved_once(self):
        first = documents_directory()

        self.assertTrue(first.is_absolute())
        self.assertIs(first, documents_directory())


if __name__ == "__main__":
    unittest.main()
