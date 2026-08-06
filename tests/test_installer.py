import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.errors import InstallError
from sprocket_mod_manager.hashing import sha256_file
from sprocket_mod_manager.installer import Installer, sprocket_is_running
from sprocket_mod_manager.models import (
    PreparedFile,
    PreparedPackage,
    PreparedPlan,
    RegistryPackage,
    ReleaseInfo,
    ResolvedPackage,
    ResolutionPlan,
)
from sprocket_mod_manager.semver import Version
from sprocket_mod_manager.state import StateStore


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
) -> PreparedPlan:
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
        install={
            "mode": "xunity-translation",
            "scan_dlls": False,
            "exclude": [],
            "overrides": [],
        },
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
    return PreparedPlan(plan, [PreparedPackage(resolved, files=prepared_files)], root)


class InstallerTests(unittest.TestCase):
    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
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
            archives = sorted((root / "app" / "backups" / "AutoTranslator").glob("*.zip"))
            self.assertEqual(len(archives), 1)
            with zipfile.ZipFile(archives[0]) as archive:
                self.assertEqual(archive.read("old.txt"), b"old")
                self.assertEqual(archive.read("stale/cache.txt"), b"cache")
                self.assertNotIn("Config.ini", archive.namelist())
            state = store.load()
            self.assertEqual(
                state["packages"]["test.translation"]["install_mode"],
                "xunity-translation",
            )

    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
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

    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
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

            archives = sorted((root / "app" / "backups" / "AutoTranslator").glob("*.zip"))
            self.assertEqual(len(archives), 5)
            self.assertEqual(len({archive.name for archive in archives}), 5)

    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
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
                "sprocket_mod_manager.installer.zipfile.ZipFile",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaisesRegex(InstallError, "cannot archive"):
                    installer.apply(prepared_translation(root), game)

            self.assertEqual((game / "AutoTranslator" / "old.txt").read_bytes(), b"old")
            self.assertFalse((game / "AutoTranslator" / "Config.ini").exists())

    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
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

    def test_process_check_treats_missing_tasklist_stdout_as_not_running(self):
        with (
            patch("sprocket_mod_manager.installer.os.name", "nt"),
            patch(
                "sprocket_mod_manager.installer.subprocess.run",
                return_value=SimpleNamespace(stdout=None),
            ),
        ):
            self.assertFalse(sprocket_is_running())

    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
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

    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
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

    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
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

    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
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


if __name__ == "__main__":
    unittest.main()
