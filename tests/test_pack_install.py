"""整合包的安装与卸载：成员是依赖，卸载按 `requested` 级联。

装包时成员是**被带进来**的（`requested=False`），卸载包会带走它们；用户自己点过名、或显式改过
版本的成员留下；成员还被别的包需要时留下。这些全部来自现有的卸载闭包，整合包不另开一条路。
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

from sprocket_mod_manager.domain.errors import InstallError
from sprocket_mod_manager.domain.models import PACK_KIND, RegistryPackage
from sprocket_mod_manager.infrastructure.installer import Installer
from sprocket_mod_manager.infrastructure.state import StateStore

PACK = "test.pack"
FIRST = "test.first"
SECOND = "test.second"
CONSUMER = "test.consumer"
OVERRIDE = "Mods/PackOverride.dll"
FIRST_FILE = "Mods/First.dll"
SECOND_FILE = "Mods/Second.dll"
CONSUMER_FILE = "Mods/Consumer.dll"


def pack_entry(*members: str) -> RegistryPackage:
    return replace(
        installer_package(PACK),
        kind=PACK_KIND,
        category="pack",
        members=tuple({"id": item, "version": "*", "when": "*"} for item in members),
    )


def pack_plan(
        root: Path,
        *,
        members: tuple[str, ...] = (FIRST, SECOND),
        entries: list[tuple[RegistryPackage, list[tuple[str, bytes]]]] | None = None,
):
    """一份整合包计划：包自己带一个 override 文件，成员各自带自己的文件。

    成员的依赖边按求解器的口径补进 `ResolvedPackage` —— 真身上它们来自条目的 `members`。
    """
    if entries is None:
        entries = [
            (installer_package(FIRST), [(FIRST_FILE, b"first")]),
            (installer_package(SECOND), [(SECOND_FILE, b"second")]),
        ]
    prepared = combined_plan(
        root,
        [(pack_entry(*members), [(OVERRIDE, b"override")]), *entries],
        root_id=PACK,
    )
    for item in prepared.packages:
        if item.resolved.package.id == PACK:
            item.resolved = replace(item.resolved, dependency_ids=tuple(members))
    return prepared


class PackInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        running = patch(
            "sprocket_mod_manager.infrastructure.installer.sprocket_is_running",
            return_value=False,
        )
        running.start()
        self.addCleanup(running.stop)

    def test_a_pack_records_its_kind_its_members_and_its_own_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)

            installer.apply(pack_plan(root), game)

            packages = store.load()["packages"]
            self.assertEqual(packages[PACK]["kind"], PACK_KIND)
            self.assertEqual(packages[PACK]["dependencies"], [FIRST, SECOND])
            self.assertTrue(packages[PACK]["requested"], "包是点名装的")
            self.assertFalse(packages[FIRST]["requested"], "成员是包带进来的")
            self.assertFalse(packages[SECOND]["requested"])
            self.assertEqual((game / OVERRIDE).read_bytes(), b"override")
            self.assertEqual(store.load()["files"][OVERRIDE]["owners"], [PACK])

    def test_removing_the_pack_takes_the_members_it_brought(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            installer.apply(pack_plan(root), game)

            removed, warnings = installer.remove(PACK, game)

            self.assertEqual(removed, sorted([FIRST, PACK, SECOND]))
            self.assertEqual(warnings, [])
            self.assertEqual(store.load()["packages"], {})
            self.assertFalse((game / OVERRIDE).exists())
            self.assertFalse((game / FIRST_FILE).exists())
            self.assertFalse((game / SECOND_FILE).exists())

    def test_a_member_the_user_installed_survives_the_pack_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            installer.apply(
                combined_plan(
                    root,
                    [(installer_package(FIRST), [(FIRST_FILE, b"first")])],
                    root_id=FIRST,
                ),
                game,
            )

            installer.apply(
                pack_plan(root, entries=[(installer_package(SECOND), [(SECOND_FILE, b"second")])]),
                game,
            )
            removed, _warnings = installer.remove(PACK, game)

            self.assertEqual(removed, sorted([PACK, SECOND]))
            self.assertEqual(sorted(store.load()["packages"]), [FIRST])
            self.assertTrue((game / FIRST_FILE).is_file(), "自己点名装过的成员留下")

    def test_a_member_another_package_needs_survives_the_pack_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            installer.apply(pack_plan(root), game)
            consumer = combined_plan(
                root,
                [(installer_package(CONSUMER), [(CONSUMER_FILE, b"consumer")])],
                root_id=CONSUMER,
            )
            for item in consumer.packages:
                item.resolved = replace(item.resolved, dependency_ids=(FIRST,))
            installer.apply(consumer, game)

            removed, _warnings = installer.remove(PACK, game)

            self.assertEqual(removed, sorted([PACK, SECOND]))
            self.assertEqual(sorted(store.load()["packages"]), [CONSUMER, FIRST])
            self.assertTrue((game / FIRST_FILE).is_file())

    def test_removing_a_member_the_pack_needs_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            installer.apply(pack_plan(root), game)

            with self.assertRaisesRegex(InstallError, f"{FIRST} is required by: {PACK}"):
                installer.remove(FIRST, game)

    def test_a_pack_whose_member_is_already_installed_still_records_the_edge(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            installer.apply(
                combined_plan(
                    root,
                    [(installer_package(SECOND), [(SECOND_FILE, b"second")])],
                    root_id=SECOND,
                ),
                game,
            )

            installer.apply(
                pack_plan(root, entries=[(installer_package(FIRST), [(FIRST_FILE, b"first")])]),
                game,
            )

            packages = store.load()["packages"]
            self.assertEqual(packages[PACK]["dependencies"], [FIRST, SECOND])
            self.assertTrue(packages[SECOND]["requested"], "装着的那一版没被这次重铺，点名标记照旧")
            self.assertEqual(sorted(packages), [FIRST, PACK, SECOND])


if __name__ == "__main__":
    unittest.main()
