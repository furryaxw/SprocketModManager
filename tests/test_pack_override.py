"""整合包覆盖模组资源文件：还原点在哪、什么时候还原、什么时候不还原。

包要换掉别的包的文件只有一条路 —— `install.mode: "patch"`。标准模式的包去写别人的文件会被
`apply` 直接拒掉（`... is shared with ... and cannot be replaced`），这是有意的：静默覆盖会让
被覆盖的那个包在记录里继续声称一份内容已经变了、摘要也对不上的文件。

补丁的还原点是**第一次替换之前**那份内容，存在 `<game>/SprocketModManager/backup/patched/<路径>`：
包更新不会重写它，所以"包更新过之后才卸载"还原回来的仍是模组原来那份。
`install.replace`（整体接管类型）走另一套：整个供给目录按类型归档，卸载时整目录还原。
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path, PurePosixPath
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_patch_install import combined_plan, game_dir
from test_patch_install import package as installer_package

from sprocket_mod_manager.domain.errors import InstallError
from sprocket_mod_manager.domain.models import PACK_KIND
from sprocket_mod_manager.infrastructure.installer import Installer
from sprocket_mod_manager.infrastructure.state import StateStore

MOD = "test.resource-mod"
PACK = "test.pack"
OTHER_PACK = "test.other-pack"
REPLACING = "test.replacing-pack"
TYPE = "sprocket:streamingassets"
DIRECTORY = PurePosixPath("Sprocket_Data/StreamingAssets")
RESOURCE = "Sprocket_Data/StreamingAssets/Parts/foo.json"
EXTRA = "Sprocket_Data/StreamingAssets/Parts/pack-extra.json"
MOD_BYTES = b"the mod's own resource"
PACK_V1 = b"pack override v1"
PACK_V2 = b"pack override v2"

RULE = {"match": "**", "type": TYPE, "layout": "tree"}


def patch_pack(package_id: str = PACK, *, replaces: tuple[str, ...] = ()):
    """补丁模式的包：写进别人已经占着的那条路径，替换前的内容先归档。"""
    install = {"mode": "patch", "files": [dict(RULE)], "scan_dlls": False, "exclude": []}
    if replaces:
        install["replace"] = list(replaces)
    return replace(
        installer_package(package_id, mode="patch"),
        kind=PACK_KIND,
        category="pack",
        install=install,
        file_rules=(dict(RULE),),
    )


def prepare(root: Path, entries, *, root_id: str, directories: dict[str, PurePosixPath] | None = None):
    prepared = combined_plan(root, entries, root_id=root_id)
    if directories:
        prepared.install_directories = directories
    return prepared


def install_mod(root: Path, game: Path, store: StateStore) -> Installer:
    installer = Installer(root / "app", store)
    installer.apply(
        prepare(
            root,
            [(installer_package(MOD), [(RESOURCE, MOD_BYTES)])],
            root_id=MOD,
            directories={TYPE: DIRECTORY},
        ),
        game,
    )
    return installer


class PackOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        running = patch(
            "sprocket_mod_manager.infrastructure.installer.sprocket_is_running",
            return_value=False,
        )
        running.start()
        self.addCleanup(running.stop)
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.game = game_dir(self.root)
        self.store = StateStore(self.root / "app" / "installed.json")
        self.addCleanup(self._temporary.cleanup)

    def read(self, relative: str) -> bytes:
        return (self.game / relative).read_bytes()

    def archive(self, relative: str) -> Path:
        return self.game / "SprocketModManager" / "backup" / "patched" / relative

    def test_a_patch_pack_overrides_a_mod_resource_and_uninstall_restores_it(self):
        installer = install_mod(self.root, self.game, self.store)
        installer.apply(
            prepare(
                self.root,
                [(patch_pack(), [(RESOURCE, PACK_V1), (EXTRA, b"pack only")])],
                root_id=PACK,
                directories={TYPE: DIRECTORY},
            ),
            self.game,
        )

        self.assertEqual(self.read(RESOURCE), PACK_V1, "包的内容盖住了模组那份")
        self.assertEqual(self.archive(RESOURCE).read_bytes(), MOD_BYTES, "替换前那份进了归档")

        removed, warnings = installer.remove(PACK, self.game)

        self.assertEqual(removed, [PACK])
        self.assertEqual(warnings, [])
        self.assertEqual(self.read(RESOURCE), MOD_BYTES, "卸载包把模组那份放回来")
        self.assertFalse((self.game / EXTRA).exists(), "包自己新增的文件跟着走")
        state = self.store.load()
        self.assertEqual(state["files"][RESOURCE]["owners"], [MOD])
        self.assertEqual(
            state["files"][RESOURCE]["sha256"], hashlib.sha256(MOD_BYTES).hexdigest(),
            "记录里的摘要也回到模组那份",
        )
        self.assertFalse(self.archive(RESOURCE).exists(), "归档随最后一个补丁包一起清掉")

    def test_updating_a_patch_pack_keeps_the_original_restore_point(self):
        installer = install_mod(self.root, self.game, self.store)
        installer.apply(
            prepare(
                self.root,
                [(patch_pack(), [(RESOURCE, PACK_V1)])],
                root_id=PACK,
                directories={TYPE: DIRECTORY},
            ),
            self.game,
        )
        installer.apply(
            prepare(
                self.root,
                [(patch_pack(), [(RESOURCE, PACK_V2)])],
                root_id=PACK,
                directories={TYPE: DIRECTORY},
            ),
            self.game,
        )

        self.assertEqual(self.read(RESOURCE), PACK_V2, "更新后是 v2 的内容")
        self.assertEqual(self.archive(RESOURCE).read_bytes(), MOD_BYTES, "归档仍是模组那份")

        installer.remove(PACK, self.game)

        self.assertEqual(self.read(RESOURCE), MOD_BYTES, "更新过也照样还原模组原来那份")

    def test_a_patch_packs_own_new_file_is_not_resurrected_by_an_update(self):
        """包自己新增的文件没有可还原的原件：更新不该把上一版的内容归档成"补丁之前的内容"。"""
        installer = Installer(self.root / "app", self.store)
        for content in (PACK_V1, PACK_V2):
            installer.apply(
                prepare(
                    self.root,
                    [(patch_pack(), [(RESOURCE, content), (EXTRA, content)])],
                    root_id=PACK,
                    directories={TYPE: DIRECTORY},
                ),
                self.game,
            )

        self.assertEqual(self.read(EXTRA), PACK_V2)

        installer.remove(PACK, self.game)

        self.assertFalse((self.game / EXTRA).exists(), "包自己新增的文件跟着包走")
        self.assertFalse((self.game / RESOURCE).exists())
        self.assertFalse(self.archive(EXTRA).exists(), "没有原件就不该有归档")

    def test_reinstalling_the_mod_while_the_pack_patches_it_is_refused(self):
        """包还占着这条路径时，模组自己重装/更新会被拒 —— 不静默把补丁顶掉。"""
        installer = install_mod(self.root, self.game, self.store)
        installer.apply(
            prepare(
                self.root,
                [(patch_pack(), [(RESOURCE, PACK_V1)])],
                root_id=PACK,
                directories={TYPE: DIRECTORY},
            ),
            self.game,
        )

        with self.assertRaisesRegex(InstallError, f"is shared with {PACK} and cannot be replaced"):
            installer.apply(
                prepare(
                    self.root,
                    [(installer_package(MOD), [(RESOURCE, b"mod reinstalled")])],
                    root_id=MOD,
                    directories={TYPE: DIRECTORY},
                ),
                self.game,
            )

        self.assertEqual(self.read(RESOURCE), PACK_V1, "拒绝之后磁盘没有被动过")

    def test_uninstalling_the_mod_while_the_pack_patches_it_keeps_the_patch(self):
        """模组走了、包还在：补丁的内容继续在场（包还要用它）；等包也走了才把原件放回来。"""
        installer = install_mod(self.root, self.game, self.store)
        installer.apply(
            prepare(
                self.root,
                [(patch_pack(), [(RESOURCE, PACK_V1)])],
                root_id=PACK,
                directories={TYPE: DIRECTORY},
            ),
            self.game,
        )

        installer.remove(MOD, self.game)

        self.assertEqual(self.read(RESOURCE), PACK_V1, "包还占着这条路径，补丁的内容留着")

        installer.remove(PACK, self.game)

        self.assertEqual(self.read(RESOURCE), MOD_BYTES, "包也走了，原件放回来")
        self.assertEqual(self.store.load()["files"], {}, "没有归属的文件不再进记录")

    def test_a_standard_pack_cannot_replace_a_mod_resource(self):
        installer = install_mod(self.root, self.game, self.store)
        standard = replace(installer_package(PACK), kind=PACK_KIND, category="pack")

        with self.assertRaisesRegex(InstallError, f"is shared with {MOD} and cannot be replaced"):
            installer.apply(
                prepare(
                    self.root,
                    [(standard, [(RESOURCE, PACK_V1)])],
                    root_id=PACK,
                    directories={TYPE: DIRECTORY},
                ),
                self.game,
            )

        self.assertEqual(self.read(RESOURCE), MOD_BYTES, "拒绝之后磁盘没有被动过")

    def test_two_patch_packs_on_one_path_restore_only_after_the_last_one_leaves(self):
        installer = install_mod(self.root, self.game, self.store)
        for package_id, content in ((PACK, PACK_V1), (OTHER_PACK, PACK_V2)):
            installer.apply(
                prepare(
                    self.root,
                    [(patch_pack(package_id), [(RESOURCE, content)])],
                    root_id=package_id,
                    directories={TYPE: DIRECTORY},
                ),
                self.game,
            )

        installer.remove(PACK, self.game)

        self.assertEqual(self.read(RESOURCE), PACK_V2, "另一个补丁还在场，不还原")
        self.assertEqual(self.archive(RESOURCE).read_bytes(), MOD_BYTES)

        installer.remove(OTHER_PACK, self.game)

        self.assertEqual(self.read(RESOURCE), MOD_BYTES, "最后一个补丁走了才还原")

    def test_a_replacing_pack_cannot_take_over_a_directory_a_mod_owns_files_in(self):
        """整体接管不吞别人的文件：目录里有非接管包名下的文件就直接拒绝。"""
        installer = install_mod(self.root, self.game, self.store)

        with self.assertRaisesRegex(
            InstallError, f"is managed by a non-replacing package: {MOD}"
        ):
            installer.apply(
                prepare(
                    self.root,
                    [(patch_pack(REPLACING, replaces=(TYPE,)), [(RESOURCE, PACK_V1)])],
                    root_id=REPLACING,
                    directories={TYPE: DIRECTORY},
                ),
                self.game,
            )

        self.assertEqual(self.read(RESOURCE), MOD_BYTES)

    def test_a_replacing_pack_restores_the_directory_on_uninstall(self):
        installer = Installer(self.root / "app", self.store)
        (self.game / "Sprocket_Data" / "StreamingAssets" / "Parts").mkdir(parents=True)
        handmade = self.game / "Sprocket_Data" / "StreamingAssets" / "Parts" / "hand-made.json"
        handmade.write_bytes(b"hand made, never installed by the manager")

        installer.apply(
            prepare(
                self.root,
                [(patch_pack(REPLACING, replaces=(TYPE,)), [(RESOURCE, PACK_V1)])],
                root_id=REPLACING,
                directories={TYPE: DIRECTORY},
            ),
            self.game,
        )

        self.assertEqual(self.read(RESOURCE), PACK_V1)
        self.assertFalse(handmade.exists(), "接管时整个目录被清空")

        installer.remove(REPLACING, self.game)

        self.assertTrue(handmade.is_file(), "卸载后目录整份还原")
        self.assertEqual(handmade.read_bytes(), b"hand made, never installed by the manager")

    def test_updating_a_replacing_pack_keeps_the_original_restore_point(self):
        installer = Installer(self.root / "app", self.store)
        (self.game / "Sprocket_Data" / "StreamingAssets" / "Parts").mkdir(parents=True)
        handmade = self.game / "Sprocket_Data" / "StreamingAssets" / "Parts" / "hand-made.json"
        handmade.write_bytes(b"hand made, never installed by the manager")

        for content in (PACK_V1, PACK_V2):
            installer.apply(
                prepare(
                    self.root,
                    [(patch_pack(REPLACING, replaces=(TYPE,)), [(RESOURCE, content)])],
                    root_id=REPLACING,
                    directories={TYPE: DIRECTORY},
                ),
                self.game,
            )

        self.assertEqual(self.read(RESOURCE), PACK_V2, "更新后是 v2 的内容")
        self.assertEqual(
            len(sorted((self.game / "SprocketModManager" / "backup" / "replaced").rglob("*.zip"))), 1,
            "更新不产生新的还原点",
        )

        installer.remove(REPLACING, self.game)

        self.assertTrue(
            handmade.is_file(),
            "接管前那份才是还原点：更新不该把它换成包自己上一个版本的内容",
        )


if __name__ == "__main__":
    unittest.main()
