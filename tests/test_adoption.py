import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.models import RegistryPackage, ReleaseAsset, ReleaseInfo
from sprocket_mod_manager.registry import Registry
from sprocket_mod_manager.semver import Version
from sprocket_mod_manager.service import ModManagerService


def package(
    package_id: str,
    file_name: str,
    content: bytes,
    *,
    target: str = "Mods",
    versions: tuple[str, ...] = ("1.0.0",),
) -> RegistryPackage:
    digest = hashlib.sha256(content).hexdigest()
    releases = tuple(
        ReleaseInfo(
            id=index,
            tag=f"v{version}",
            version=Version.parse(version),
            prerelease=False,
            published_at="",
            assets=(
                ReleaseAsset(
                    id=index,
                    name=file_name,
                    size=len(content),
                    download_url=(
                        f"https://github.com/test/repo/releases/download/v{version}/{file_name}"
                    ),
                    digest=f"sha256:{digest}",
                ),
            ),
        )
        for index, version in enumerate(versions, start=1)
    )
    return RegistryPackage(
        id=package_id,
        name=file_name.removesuffix(".dll"),
        authors=("test",),
        repository="test/repo",
        license="MIT",
        display_name={"en": package_id},
        description={"en": "test"},
        release={"assets": {"include": [file_name], "exclude": []}},
        dependencies=(),
        install={
            "scan_dlls": True,
            "exclude": [],
            "overrides": [{"match": file_name, "target": target}],
        },
        category="utility",
        tags=(),
        releases=releases,
    )


class ExistingModsAdoptionTests(unittest.TestCase):
    def game(self, root: Path) -> Path:
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        return game

    @patch("sprocket_mod_manager.installer.sprocket_is_running", return_value=False)
    def test_exact_release_dll_is_adopted_and_can_be_removed(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            content = b"published mod"
            installed_file = game / "Mods" / "TestMod.dll"
            installed_file.write_bytes(content)
            service = ModManagerService(root / "app")
            item = package("test.mod", installed_file.name, content)
            service.registry = Registry([item])

            adopted = service.adopt_existing(game)
            state = service._installer_for(game).state_store.load()

            self.assertEqual([record.package_id for record in adopted], [item.id])
            self.assertTrue(state["packages"][item.id]["requested"])
            self.assertTrue(state["packages"][item.id]["adopted"])
            self.assertEqual(state["packages"][item.id]["version"], "1.0.0")
            self.assertFalse(state["files"]["Mods/TestMod.dll"]["preexisting"])
            self.assertTrue(state["files"]["Mods/TestMod.dll"]["adopted"])

            removed, warnings = service.remove(item.id, game)
            self.assertEqual(removed, [item.id])
            self.assertEqual(warnings, [])
            self.assertFalse(installed_file.exists())

    def test_hash_mismatch_is_left_unmanaged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            (game / "Mods" / "TestMod.dll").write_bytes(b"local build")
            service = ModManagerService(root / "app")
            service.registry = Registry([package("test.mod", "TestMod.dll", b"release build")])

            self.assertEqual(service.adopt_existing(game), ())
            self.assertEqual(service.installed(game), {})

    def test_install_target_mismatch_is_left_unmanaged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            content = b"plugin"
            (game / "Mods" / "TestPlugin.dll").write_bytes(content)
            service = ModManagerService(root / "app")
            service.registry = Registry(
                [package("test.plugin", "TestPlugin.dll", content, target="Plugins")]
            )

            self.assertEqual(service.adopt_existing(game), ())

    def test_same_digest_in_multiple_versions_is_left_ambiguous(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            content = b"same release payload"
            (game / "Mods" / "TestMod.dll").write_bytes(content)
            service = ModManagerService(root / "app")
            service.registry = Registry(
                [
                    package(
                        "test.mod",
                        "TestMod.dll",
                        content,
                        versions=("1.1.0", "1.0.0"),
                    )
                ]
            )

            self.assertEqual(service.adopt_existing(game), ())

    def test_file_matching_multiple_packages_is_left_ambiguous(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            content = b"shared payload"
            (game / "Mods" / "Shared.dll").write_bytes(content)
            service = ModManagerService(root / "app")
            service.registry = Registry(
                [
                    package("test.one", "Shared.dll", content),
                    package("test.two", "Shared.dll", content),
                ]
            )

            self.assertEqual(service.adopt_existing(game), ())
            self.assertEqual(service.installed(game), {})


if __name__ == "__main__":
    unittest.main()
