import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.domain.models import RegistryPackage, ReleaseAsset, ReleaseInfo
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.domain.semver import Version
from sprocket_mod_manager.application.service import ModManagerService


def package(
    package_id: str,
    file_name: str,
    content: bytes,
    *,
    target: str = "Mods",
    versions: tuple[str, ...] = ("1.0.0",),
    repository: str = "test/repo",
    digest_of: bytes | None = None,
) -> RegistryPackage:
    # digest_of 允许故意写一个"对不上"的发布摘要，用来证明认领不依赖 SHA-256。
    digest = hashlib.sha256(content if digest_of is None else digest_of).hexdigest()
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
        repository=repository,
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


FIXTURE_MOD = Path(__file__).resolve().parent / "fixtures" / "dll_metadata" / "dll" / "FixtureMod.dll"


class ExistingModsAdoptionTests(unittest.TestCase):
    def game(self, root: Path) -> Path:
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        return game

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_adoption_persists_the_metadata_cache(self, _running):
        """认领也会解析 DLL 元数据，所以它必须自己把缓存落盘（否则调用方一漏就永远是冷的）。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            installed_file = game / "Mods" / "FixtureMod.dll"
            installed_file.write_bytes(FIXTURE_MOD.read_bytes())
            app_dir = root / "app"
            service = ModManagerService(app_dir)
            item = package("fixture.sprocket-mod", "FixtureMod.dll", installed_file.read_bytes(),
                           repository="fixture/FixtureMod")
            service.registry = Registry([item])

            adopted = service.adopt_existing(game)

            self.assertEqual([record.package_id for record in adopted], [item.id])
            self.assertTrue((game / "SprocketModManager" / "file-metadata.json").is_file(),
                            "认领路径必须把解析结果写进 <game>/SprocketModManager/file-metadata.json")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_declared_id_claims_even_when_the_digest_does_not_match(self, _running):
        # 认领路径只看 DLL 内置身份：Release 摘要故意写成别的字节，依然必须认领。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            installed_file = game / "Mods" / "FixtureMod.dll"
            installed_file.write_bytes(FIXTURE_MOD.read_bytes())
            service = ModManagerService(root / "app")
            item = package(
                "fixture.sprocket-mod",
                "FixtureMod.dll",
                installed_file.read_bytes(),
                repository="fixture/FixtureMod",
                digest_of=b"a completely different payload",
            )
            service.registry = Registry([item])

            adopted = service.adopt_existing(game)
            state = service._installer_for(game).state_store.load()

            self.assertEqual([record.package_id for record in adopted], [item.id])
            self.assertEqual(adopted[0].files, ("Mods/FixtureMod.dll",))
            self.assertEqual(state["packages"][item.id]["version"], "1.0.0",
                             "version falls back to the newest release when the DLL version matches none")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_repository_claims_when_the_package_id_differs(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            (game / "Mods" / "FixtureMod.dll").write_bytes(FIXTURE_MOD.read_bytes())
            service = ModManagerService(root / "app")
            item = package(
                "someone-else.renamed-package",
                "FixtureMod.dll",
                FIXTURE_MOD.read_bytes(),
                repository="fixture/FixtureMod",
                digest_of=b"mismatched on purpose",
            )
            service.registry = Registry([item])

            adopted = service.adopt_existing(game)

            self.assertEqual([record.package_id for record in adopted], [item.id],
                             "Sprocket.Mod.Repository identifies the package when the id differs")

    def test_exact_version_is_preferred_when_it_matches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            (game / "Mods" / "FixtureMod.dll").write_bytes(FIXTURE_MOD.read_bytes())
            service = ModManagerService(root / "app")
            item = package(
                "fixture.sprocket-mod",
                "FixtureMod.dll",
                FIXTURE_MOD.read_bytes(),
                repository="fixture/FixtureMod",
                digest_of=b"mismatched on purpose",
                versions=("2.0.0", "1.2.3", "1.0.0"),
            )
            service.registry = Registry([item])

            adopted = service.adopt_existing(game)

            self.assertEqual([record.version for record in adopted], ["1.2.3"],
                             "the DLL's MelonInfo version picks the matching release when one exists")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
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
            self.assertEqual(state["packages"][item.id]["version"], "1.0.0")

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

    def test_dll_below_the_root_is_left_unmanaged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            content = b"published mod"
            nested = game / "Mods" / "Nested" / "TestMod.dll"
            nested.parent.mkdir()
            nested.write_bytes(content)
            service = ModManagerService(root / "app")
            service.registry = Registry([package("test.mod", "TestMod.dll", content)])

            self.assertEqual(service.adopt_existing(game), ())
            self.assertEqual(service.installed(game), {})

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_exact_release_userlib_is_adopted(self, _running):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            content = b"published library"
            installed_file = game / "UserLibs" / "TestLibrary.dll"
            installed_file.parent.mkdir()
            installed_file.write_bytes(content)
            service = ModManagerService(root / "app")
            item = package(
                "test.library",
                installed_file.name,
                content,
                target="UserLibs",
            )
            service.registry = Registry([item])

            adopted = service.adopt_existing(game)

            self.assertEqual([record.package_id for record in adopted], [item.id])
            self.assertEqual(adopted[0].files, ("UserLibs/TestLibrary.dll",))

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
