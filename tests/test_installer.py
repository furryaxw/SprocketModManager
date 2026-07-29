import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.hashing import sha256_file
from sprocket_mod_manager.installer import Installer
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


class InstallerTests(unittest.TestCase):
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
