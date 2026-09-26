import json
import shutil
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from sprocket_mod_manager.domain.errors import InstallConflictError, InstallError
from sprocket_mod_manager.utilities.checksums import sha256_file
from sprocket_mod_manager.infrastructure.installer import Installer
from sprocket_mod_manager.domain.models import (
    PreparedFile,
    PreparedPackage,
    PreparedPlan,
    RegistryPackage,
    ReleaseInfo,
    ResolvedPackage,
    ResolutionPlan,
)
from sprocket_mod_manager.domain.semver import Version
from sprocket_mod_manager.infrastructure.state import StateStore


def registry_package(package_id="test.mod", name="TestMod"):
    return RegistryPackage(
        id=package_id,
        name=name,
        authors=("test",),
        repository="test/repo",
        license="MIT",
        display_name={"en": "Test"},
        description={"en": "Test"},
        release={},
        dependencies=(),
        install={},
        category="utility",
        tags=(),
    )


def prepared(root: Path, version: str, content: bytes) -> PreparedPlan:
    source = root / f"source-{version}.dll"
    source.write_bytes(content)
    package = registry_package()
    release = ReleaseInfo(1, f"v{version}", Version.parse(version), False, "", ())
    resolved = ResolvedPackage(package, release, ())
    plan = ResolutionPlan(package.id, (resolved,))
    file = PreparedFile(package.id, source, source.name, "Mods/TestMod.dll", sha256_file(source))
    return PreparedPlan(plan, [PreparedPackage(resolved, files=[file])], root)


def prepared_with_dependency(root: Path) -> PreparedPlan:
    dependency_source = root / "dependency.dll"
    root_source = root / "root.dll"
    dependency_source.write_bytes(b"dependency")
    root_source.write_bytes(b"root")
    dependency_package = registry_package("test.lib", "TestLib")
    root_package = registry_package()
    dependency_release = ReleaseInfo(1, "v1.0.0", Version.parse("1.0.0"), False, "", ())
    root_release = ReleaseInfo(2, "v1.0.0", Version.parse("1.0.0"), False, "", ())
    dependency = ResolvedPackage(dependency_package, dependency_release, ())
    root_item = ResolvedPackage(root_package, root_release, (dependency_package.id,))
    plan = ResolutionPlan(root_package.id, (dependency, root_item))
    return PreparedPlan(
        plan,
        [
            PreparedPackage(
                dependency,
                files=[
                    PreparedFile(
                        dependency_package.id,
                        dependency_source,
                        dependency_source.name,
                        "UserLibs/TestLib.dll",
                        sha256_file(dependency_source),
                    )
                ],
            ),
            PreparedPackage(
                root_item,
                files=[
                    PreparedFile(
                        root_package.id,
                        root_source,
                        root_source.name,
                        "Mods/TestMod.dll",
                        sha256_file(root_source),
                    )
                ],
            ),
        ],
        root,
    )


def prepared_translation(
    root: Path,
    package_id: str = "test.translation",
    files: dict[str, bytes] | None = None,
    *,
    replaces: bool = True,
) -> PreparedPlan:
    """一个整体接管 `xunity:translation` 供给目录的翻译包。"""
    install = {
        "mode": "patch",
        "scan_dlls": False,
        "exclude": [],
    }
    if replaces:
        install["replace"] = ["xunity:translation"]
    package = RegistryPackage(
        id=package_id,
        name="TestTranslation",
        authors=("test",),
        repository="test/translation",
        license="MIT",
        display_name={"en": "Test translation"},
        description={"en": "Test translation"},
        release={},
        dependencies=(),
        install=install,
        category="translation",
        tags=(),
    )
    release = ReleaseInfo(1, "v1.0.0", Version.parse("1.0.0"), False, "", ())
    resolved = ResolvedPackage(package, release, ())
    prepared_files = []
    for index, (relative, content) in enumerate(
        (files or {"Config.ini": b"new config"}).items()
    ):
        source = root / f"translation-source-{index}"
        source.write_bytes(content)
        prepared_files.append(
            PreparedFile(
                package.id,
                source,
                relative,
                f"AutoTranslator/{relative}",
                sha256_file(source),
            )
        )
    plan = ResolutionPlan(package.id, (resolved,))
    return PreparedPlan(
        plan,
        [PreparedPackage(resolved, files=prepared_files)],
        root,
        {"xunity:translation": PurePosixPath("AutoTranslator")},
    )


def prepared_modloader(
    root: Path,
    *,
    package_id: str = "lavagang.melonloader",
    target: str = "{Sprocket}",
    files: dict[str, bytes] | None = None,
) -> PreparedPlan:
    """一个基础运行时（`kind: modloader`）的 `install.payload` 计划。"""
    rule = {"match": "**", "target": target, "layout": "tree"}
    package = replace(
        registry_package(package_id, "MelonLoader"),
        kind="modloader",
        install={"payload": [rule], "exclude": []},
        payload_rules=(rule,),
    )
    release = ReleaseInfo(7, "v0.7.3", Version.parse("0.7.3"), False, "", ())
    resolved = ResolvedPackage(package, release, ())
    prepared_files = []
    for index, (relative, content) in enumerate(
        (files or {"MelonLoader/net6/MelonLoader.dll": b"loader"}).items()
    ):
        source = root / f"modloader-source-{index}"
        source.write_bytes(content)
        prepared_files.append(
            PreparedFile(package_id, source, relative, relative, sha256_file(source))
        )
    plan = ResolutionPlan(package_id, (resolved,))
    return PreparedPlan(plan, [PreparedPackage(resolved, files=prepared_files)], root)


def prepared_target(
    root: Path,
    target: str,
    content: bytes,
    *,
    package_id: str = "test.other",
    supply: dict[str, str] | None = None,
) -> PreparedPlan:
    """一个普通包，落在指定目标路径（用来在别处占住一个目录）。"""
    source = root / f"{package_id}-source.dll"
    source.write_bytes(content)
    package = replace(
        registry_package(package_id, "Other"),
        supply=dict(supply or {}),
    )
    release = ReleaseInfo(9, "v1.0.0", Version.parse("1.0.0"), False, "", ())
    resolved = ResolvedPackage(package, release, ())
    file = PreparedFile(package_id, source, source.name, target, sha256_file(source))
    plan = ResolutionPlan(package_id, (resolved,))
    return PreparedPlan(plan, [PreparedPackage(resolved, files=[file])], root)


class InstallerTests(unittest.TestCase):
    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_translation_install_replaces_entire_autotranslator_directory(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            (game / "AutoTranslator" / "stale").mkdir(parents=True)
            (game / "Sprocket.exe").write_bytes(b"")
            (game / "AutoTranslator" / "old.txt").write_bytes(b"old")
            (game / "AutoTranslator" / "stale" / "cache.txt").write_bytes(b"cache")
            store = StateStore(root / "app" / "installed.json")

            Installer(root / "app", store).apply(
                prepared_translation(
                    root,
                    files={
                        "Config.ini": b"new config",
                        "Translation/zh-CN/Text/Translations.txt": b"Hello=translated",
                    },
                ),
                game,
            )

            self.assertFalse((game / "AutoTranslator" / "old.txt").exists())
            self.assertFalse((game / "AutoTranslator" / "stale").exists())
            self.assertEqual((game / "AutoTranslator" / "Config.ini").read_bytes(), b"new config")
            archives = sorted(
                (game / "SprocketModManager" / "backup" / "replaced" / "xunity-translation").glob("*.zip")
            )
            self.assertEqual(len(archives), 1)
            with zipfile.ZipFile(archives[0]) as archive:
                self.assertEqual(archive.read("old.txt"), b"old")
                self.assertEqual(archive.read("stale/cache.txt"), b"cache")
                self.assertNotIn("Config.ini", archive.namelist())
            state = store.load()
            self.assertEqual(
                state["packages"]["test.translation"]["install_mode"],
                "patch",
            )
            self.assertEqual(
                state["packages"]["test.translation"]["replaced_types"],
                ["xunity:translation"],
            )

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_translation_install_rolls_back_directory_when_state_save_fails(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            (game / "AutoTranslator").mkdir(parents=True)
            (game / "Sprocket.exe").write_bytes(b"")
            (game / "AutoTranslator" / "old.txt").write_bytes(b"old")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)

            with patch.object(store, "save", side_effect=OSError("state failure")):
                with self.assertRaisesRegex(OSError, "state failure"):
                    installer.apply(prepared_translation(root), game)

            self.assertEqual((game / "AutoTranslator" / "old.txt").read_bytes(), b"old")
            self.assertFalse((game / "AutoTranslator" / "Config.ini").exists())

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_translation_backups_keep_only_five_newest_archives(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            (game / "AutoTranslator").mkdir(parents=True)
            (game / "Sprocket.exe").write_bytes(b"")
            (game / "AutoTranslator" / "seed.txt").write_bytes(b"seed")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)

            for index in range(6):
                installer.apply(
                    prepared_translation(root, files={"Config.ini": f"version {index}".encode()}),
                    game,
                )

            archives = sorted(
                (game / "SprocketModManager" / "backup" / "replaced" / "xunity-translation").glob("*.zip")
            )
            self.assertEqual(len(archives), 5)
            self.assertEqual(len({archive.name for archive in archives}), 5)

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_translation_backup_failure_does_not_clear_directory(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            (game / "AutoTranslator").mkdir(parents=True)
            (game / "Sprocket.exe").write_bytes(b"")
            (game / "AutoTranslator" / "old.txt").write_bytes(b"old")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)

            with patch(
                "sprocket_mod_manager.infrastructure.xunity_backup.zipfile.ZipFile",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaisesRegex(InstallError, "cannot archive"):
                    installer.apply(prepared_translation(root), game)

            self.assertEqual((game / "AutoTranslator" / "old.txt").read_bytes(), b"old")
            self.assertFalse((game / "AutoTranslator" / "Config.ini").exists())

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_new_translation_replaces_previous_translation_package_state(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").write_bytes(b"")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)

            installer.apply(prepared_translation(root, "test.translation-a"), game)
            installer.apply(
                prepared_translation(root, "test.translation-b", {"Config.ini": b"second"}),
                game,
            )

            state = store.load()
            self.assertNotIn("test.translation-a", state["packages"])
            self.assertIn("test.translation-b", state["packages"])
            self.assertEqual((game / "AutoTranslator" / "Config.ini").read_bytes(), b"second")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_translation_uninstall_restores_the_whole_directory(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            (game / "AutoTranslator" / "stale").mkdir(parents=True)
            (game / "Sprocket.exe").write_bytes(b"")
            (game / "AutoTranslator" / "old.txt").write_bytes(b"old")
            (game / "AutoTranslator" / "stale" / "cache.txt").write_bytes(b"cache")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)

            installer.apply(prepared_translation(root), game)
            self.assertFalse((game / "AutoTranslator" / "old.txt").exists())

            installer.remove("test.translation", game)

            self.assertEqual((game / "AutoTranslator" / "old.txt").read_bytes(), b"old")
            self.assertEqual(
                (game / "AutoTranslator" / "stale" / "cache.txt").read_bytes(), b"cache"
            )
            self.assertFalse((game / "AutoTranslator" / "Config.ini").exists())
            self.assertNotIn("test.translation", store.load()["packages"])

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_replaced_package_still_required_is_not_displaced(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").write_bytes(b"")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)

            installer.apply(prepared_translation(root, "test.translation-a"), game)
            consumer_package = registry_package("test.consumer", "Consumer")
            consumer_release = ReleaseInfo(3, "v1.0.0", Version.parse("1.0.0"), False, "", ())
            consumer = ResolvedPackage(consumer_package, consumer_release, ("test.translation-a",))
            source = root / "consumer.dll"
            source.write_bytes(b"consumer")
            installer.apply(
                PreparedPlan(
                    ResolutionPlan("test.consumer", (consumer,)),
                    [
                        PreparedPackage(
                            consumer,
                            files=[
                                PreparedFile(
                                    "test.consumer",
                                    source,
                                    source.name,
                                    "Mods/Consumer.dll",
                                    sha256_file(source),
                                )
                            ],
                        )
                    ],
                    root,
                ),
                game,
            )

            with self.assertRaisesRegex(InstallError, "required by"):
                installer.apply(
                    prepared_translation(root, "test.translation-b", {"Config.ini": b"second"}),
                    game,
                )

            state = store.load()
            self.assertIn("test.translation-a", state["packages"])
            self.assertNotIn("test.translation-b", state["packages"])

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_update_recovers_null_file_reference_from_installed_state(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").write_bytes(b"")
            store = StateStore(root / "app" / "installed.json")
            store.path.parent.mkdir(parents=True)
            store.path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "packages": {
                            "test.mod": {
                                "id": "test.mod",
                                "name": "TestMod",
                                "version": "0.9.0",
                                "requested": True,
                                "dependencies": [],
                                "files": [None],
                            }
                        },
                        "files": {},
                    }
                ),
                encoding="utf-8",
            )
            installer = Installer(root / "app", store)

            installer.apply(prepared(root, "1.0.0", b"fixed"), game)

            state = store.load()
            self.assertEqual(state["packages"]["test.mod"]["files"], ["Mods/TestMod.dll"])
            self.assertEqual((game / "Mods" / "TestMod.dll").read_bytes(), b"fixed")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_install_update_and_remove_same_path(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").write_bytes(b"")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)

            installer.apply(prepared(root, "1.0.0", b"first"), game)
            self.assertEqual((game / "Mods" / "TestMod.dll").read_bytes(), b"first")
            self.assertEqual(store.load()["packages"]["test.mod"]["version"], "1.0.0")

            installer.apply(prepared(root, "1.1.0", b"second"), game)
            self.assertEqual((game / "Mods" / "TestMod.dll").read_bytes(), b"second")
            self.assertEqual(store.load()["packages"]["test.mod"]["version"], "1.1.0")

            removed, warnings = installer.remove("test.mod", game)
            self.assertEqual(removed, ["test.mod"])
            self.assertEqual(warnings, [])
            self.assertFalse((game / "Mods" / "TestMod.dll").exists())

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_remove_preserves_file_modified_after_install(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").write_bytes(b"")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            installer.apply(prepared(root, "1.0.0", b"managed"), game)
            installed_file = game / "Mods" / "TestMod.dll"
            installed_file.write_bytes(b"user change")

            _, warnings = installer.remove("test.mod", game)
            self.assertTrue(installed_file.is_file())
            self.assertEqual(installed_file.read_bytes(), b"user change")
            self.assertEqual(warnings, ["preserved modified file: Mods/TestMod.dll"])

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_force_conflicts_overwrites_externally_modified_managed_file(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").touch()
            installer = Installer(root / "app", StateStore(root / "app" / "installed.json"))
            installer.apply(prepared(root, "1.0.0", b"first"), game)
            target = game / "Mods" / "TestMod.dll"
            target.write_bytes(b"external change")

            with self.assertRaises(InstallConflictError):
                installer.apply(prepared(root, "1.1.0", b"second"), game)
            installer.apply(prepared(root, "1.1.0", b"second"), game, force_conflicts=True)

            self.assertEqual(target.read_bytes(), b"second")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_force_conflicts_takes_ownership_of_unmanaged_target(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            target = game / "Mods" / "TestMod.dll"
            target.parent.mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            target.write_bytes(b"unmanaged")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)

            with self.assertRaises(InstallConflictError):
                installer.apply(prepared(root, "1.0.0", b"managed"), game)
            installer.apply(prepared(root, "1.0.0", b"managed"), game, force_conflicts=True)

            self.assertIn("Mods/TestMod.dll", store.load()["files"],
                          "强制覆盖后该文件就是受管文件")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_update_over_a_disabled_file_keeps_it_disabled(self, _running):
        """`X.dll` 与 `X.dll.disable` 是同一个逻辑文件：更新写进禁用变体本身。

        照规范名去写会在禁用文件旁边多出一份同名 DLL，等于同一个模组在磁盘上有两份。
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").touch()
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            installer.apply(prepared(root, "1.0.0", b"first"), game)
            enabled = game / "Mods" / "TestMod.dll"
            disabled = enabled.with_name(enabled.name + ".disable")
            enabled.rename(disabled)

            installer.apply(prepared(root, "1.1.0", b"second"), game)

            self.assertFalse(enabled.exists())
            self.assertEqual(disabled.read_bytes(), b"second")
            self.assertEqual(
                store.load()["files"]["Mods/TestMod.dll"]["sha256"], sha256_file(disabled)
            )

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_update_removes_dependency_that_becomes_orphaned(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").write_bytes(b"")
            store = StateStore(root / "app" / "installed.json")
            installer = Installer(root / "app", store)
            installer.apply(prepared_with_dependency(root), game)
            self.assertTrue((game / "UserLibs" / "TestLib.dll").is_file())

            installer.apply(prepared(root, "2.0.0", b"root v2"), game)
            self.assertFalse((game / "UserLibs" / "TestLib.dll").exists())
            self.assertNotIn("test.lib", store.load()["packages"])


class ModloaderRecordTests(unittest.TestCase):
    """基础运行时（`kind: modloader`）不记逐文件清单：版本 + 顶层条目就是全部记录。"""

    LOADER_ID = "lavagang.melonloader"

    def _installer(self, root: Path) -> tuple[Installer, Path, StateStore]:
        game = root / "game"
        game.mkdir(parents=True, exist_ok=True)
        (game / "Sprocket.exe").write_bytes(b"")
        store = StateStore(root / "app" / "installed.json")
        return Installer(root / "app", store), game, store

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_install_records_the_payload_top_level_entries_not_a_per_file_list(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, store = self._installer(root)

            installer.apply(
                prepared_modloader(
                    root,
                    files={
                        "MelonLoader/net6/MelonLoader.dll": b"loader",
                        "MelonLoader/net6/0Harmony.dll": b"harmony",
                        "version.dll": b"proxy",
                    },
                ),
                game,
            )

            record = store.load()["packages"][self.LOADER_ID]
            self.assertEqual(record["kind"], "modloader")
            self.assertEqual(record["version"], "0.7.3")
            self.assertEqual(record["files"], [], "基础运行时不写逐文件清单")
            self.assertEqual(record["directories"], ["MelonLoader"], "记的是安装时落地的目录")
            self.assertEqual(
                [entry["path"] for entry in record["payload_files"]],
                ["version.dll"],
                "落在游戏根目录的顶层文件也要记下来（卸载时靠它搬走代理 DLL）",
            )
            self.assertEqual(
                record["payload_files"][0]["sha256"], sha256_file(game / "version.dll"),
                "顶层文件带着安装时的摘要，卸载时才敢核对内容",
            )
            self.assertTrue((game / "MelonLoader" / "net6" / "MelonLoader.dll").is_file())
            self.assertNotIn(
                "MelonLoader/net6/MelonLoader.dll",
                store.path.read_text(encoding="utf-8"),
                "落盘的记录里没有逐文件路径",
            )

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_reconcile_keeps_the_record_until_its_directories_are_gone(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, store = self._installer(root)
            installer.apply(prepared_modloader(root), game)

            self.assertEqual(installer.reconcile(game), [])
            self.assertIn(self.LOADER_ID, store.load()["packages"], "目录还在，记录必须留着")

            shutil.rmtree(game / "MelonLoader")
            dropped = installer.reconcile(game)

            self.assertIn(self.LOADER_ID, dropped)
            self.assertNotIn(self.LOADER_ID, store.load()["packages"])

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_removing_a_modloader_moves_its_tree_and_root_files_into_the_backup(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, store = self._installer(root)
            installer.apply(
                prepared_modloader(
                    root,
                    files={
                        "MelonLoader/net6/MelonLoader.dll": b"loader",
                        "version.dll": b"proxy",
                    },
                ),
                game,
            )
            self.assertTrue((game / "MelonLoader").is_dir())

            removed, warnings = installer.remove(self.LOADER_ID, game)

            backup = game / "SprocketModManager" / "backup" / "loaders" / self.LOADER_ID
            self.assertEqual(removed, [self.LOADER_ID])
            self.assertEqual(warnings, [])
            self.assertFalse((game / "MelonLoader").exists(), "加载器自己的树整棵搬走")
            self.assertFalse((game / "version.dll").exists(), "代理 DLL 一起搬走")
            self.assertEqual(
                (backup / "payload" / "MelonLoader" / "net6" / "MelonLoader.dll").read_bytes(),
                b"loader",
            )
            self.assertEqual((backup / "payload" / "version.dll").read_bytes(), b"proxy")
            manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["loader"], self.LOADER_ID)
            self.assertEqual(sorted(manifest["moved"]), ["MelonLoader", "version.dll"])
            self.assertNotIn(self.LOADER_ID, store.load()["packages"])

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_removing_a_modloader_moves_a_modified_root_file_instead_of_deleting_it(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, _store = self._installer(root)
            installer.apply(
                prepared_modloader(root, files={"version.dll": b"proxy"}), game
            )
            (game / "version.dll").write_bytes(b"hand edit")

            _removed, warnings = installer.remove(self.LOADER_ID, game)

            self.assertEqual(warnings, [])
            self.assertFalse((game / "version.dll").exists(), "搬走而不是删掉")
            self.assertEqual(
                (
                    game / "SprocketModManager" / "backup" / "loaders" / self.LOADER_ID
                    / "payload" / "version.dll"
                ).read_bytes(),
                b"hand edit",
                "用户改过的内容照样保住",
            )

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_removing_a_modloader_takes_the_other_packages_files_with_it(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, store = self._installer(root)
            installer.apply(
                prepared_modloader(root, files={"LoaderTree/core/loader.dll": b"loader"}), game
            )
            installer.apply(prepared_target(root, "LoaderTree/extra.dll", b"other"), game)
            self.assertIn("test.other", store.load()["packages"])

            _removed, warnings = installer.remove(self.LOADER_ID, game)

            self.assertEqual(warnings, [])
            self.assertFalse((game / "LoaderTree").exists(), "整棵树搬走")
            self.assertEqual(
                (
                    game / "SprocketModManager" / "backup" / "loaders" / self.LOADER_ID
                    / "payload" / "LoaderTree" / "extra.dll"
                ).read_bytes(),
                b"other",
            )
            self.assertNotIn("test.other", store.load()["packages"], "搬走的文件不再算已安装")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_removing_a_modloader_empties_the_directory_it_supplies(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, store = self._installer(root)
            installer.apply(
                prepared_modloader(
                    root, target="{Sprocket}/Mods", files={"Mods/LoaderStub.dll": b"loader"}
                ),
                game,
            )
            installer.apply(prepared_target(root, "Mods/TestMod.dll", b"mod"), game)

            _removed, warnings = installer.remove(self.LOADER_ID, game)

            self.assertEqual(warnings, [])
            self.assertFalse((game / "Mods").exists(), "供给目录整棵搬走")
            self.assertNotIn("test.other", store.load()["packages"], "目录里的模组跟着停用")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_supplier_takes_the_mods_in_the_directory_it_supplies_with_it(self, _running):
        """不是基础运行时的供给者（桥接）也一样：它供给的目录里的模组跟着搬走、重装再搬回来。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, store = self._installer(root)
            bridge = prepared_target(
                root,
                "MLLoader/MelonLoader/core.dll",
                b"core",
                package_id="test.bridge",
                supply={"melonloader:mod": "{Sprocket}/MLLoader/Mods"},
            )
            installer.apply(bridge, game)
            installer.apply(prepared_target(root, "MLLoader/Mods/TestMod.dll", b"mod"), game)

            removed, warnings = installer.remove(
                "test.bridge", game, loader_supply={"test.bridge": ("MLLoader/Mods",)}
            )

            self.assertEqual(warnings, [])
            self.assertEqual(removed, ["test.bridge"])
            self.assertFalse((game / "MLLoader" / "MelonLoader").exists(), "自己的树搬走")
            self.assertFalse((game / "MLLoader" / "Mods").exists(), "供给给模组的目录一起搬走")
            self.assertNotIn("test.other", store.load()["packages"], "供给目录里的模组跟着停用")

            installer.apply(bridge, game)

            self.assertEqual(
                (game / "MLLoader" / "Mods" / "TestMod.dll").read_bytes(),
                b"mod",
                "重装供给者把模组带回来",
            )
            self.assertIn("test.other", store.load()["packages"], "记录也跟着回来")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_loader_that_cannot_be_moved_keeps_its_install_record(self, _running):
        """搬不动就不算卸掉：记录留着、文件留着，界面上的按钮继续有意义。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, store = self._installer(root)
            installer.apply(
                prepared_modloader(
                    root,
                    files={"MelonLoader/net6/MelonLoader.dll": b"loader", "version.dll": b"proxy"},
                ),
                game,
            )

            with patch(
                "sprocket_mod_manager.infrastructure.installer.archive_location",
                side_effect=InstallError("locked"),
            ):
                removed, warnings = installer.remove(self.LOADER_ID, game)

            self.assertEqual(removed, [], "一个位置都没搬走就不算卸载")
            self.assertIn(self.LOADER_ID, store.load()["packages"], "记录必须留着")
            self.assertTrue((game / "MelonLoader" / "net6" / "MelonLoader.dll").is_file())
            self.assertTrue((game / "version.dll").is_file())
            self.assertEqual(len(warnings), 3, warnings)
            self.assertTrue(
                any("still in the game directory" in line for line in warnings), warnings
            )

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_reinstalling_the_modloader_brings_back_what_it_moved_away(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, store = self._installer(root)
            plan = prepared_modloader(root, files={"MelonLoader/net6/MelonLoader.dll": b"loader"})
            installer.apply(plan, game)
            installer.apply(prepared_target(root, "Mods/TestMod.dll", b"mod"), game)

            installer.remove(self.LOADER_ID, game, loader_supply={self.LOADER_ID: ("Mods",)})
            self.assertFalse((game / "Mods" / "TestMod.dll").exists(), "供给目录里的模组跟着搬走")
            self.assertNotIn("test.other", store.load()["packages"], "搬走的不再算已安装")

            installer.apply(plan, game)

            self.assertEqual(
                (game / "Mods" / "TestMod.dll").read_bytes(), b"mod", "重装加载器把模组带回来"
            )
            self.assertIn("test.other", store.load()["packages"], "记录也跟着回来")
            self.assertFalse(
                (game / "SprocketModManager" / "backup" / "loaders" / self.LOADER_ID).exists(),
                "搬空的归档不再留着",
            )

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_apply_reports_the_installed_file_count(self, _running):
        """加载器不逐文件记账，写入数只能由安装自己报出来；普通包也一样。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, game, _store = self._installer(root)

            installer.apply(prepared_modloader(root), game)
            self.assertEqual(installer.last_applied_files, 1, "加载器：计划里有一个目标文件")

            installer.apply(
                prepared_modloader(
                    root,
                    files={
                        "MelonLoader/net6/MelonLoader.dll": b"loader",
                        "version.dll": b"proxy",
                    },
                ),
                game,
            )
            self.assertEqual(installer.last_applied_files, 2)

            installer.apply(prepared(root, "1.0.0", b"mod"), game)
            self.assertEqual(installer.last_applied_files, 1, "普通模组同样报真实数量")


if __name__ == "__main__":
    unittest.main()
