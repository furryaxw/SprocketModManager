import hashlib
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.domain.models import RegistryPackage, ReleaseAsset, ReleaseInfo
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.domain.semver import Version
from sprocket_mod_manager.application.adoption import ExistingModsAdopter
from sprocket_mod_manager.application.service import ModManagerService
from sprocket_mod_manager.infrastructure.dll_metadata import read_dll_metadata


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
FIXTURE_DIR = FIXTURE_MOD.parent


def install_melonloader(game: Path) -> None:
    """游戏根目录的 MelonLoader 布局：认领扫描 `Mods` 之前得先检测到运行时。"""
    (game / "version.dll").touch()
    (game / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    (game / "MelonLoader" / "net6" / "MelonLoader.dll").touch()


class ExistingModsAdoptionTests(unittest.TestCase):
    def game(self, root: Path) -> Path:
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        install_melonloader(game)
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


BRIDGE_ID = "1499501762.bepinex-melonloader-loader"


def bridge_package() -> RegistryPackage:
    return RegistryPackage(
        id=BRIDGE_ID,
        name="BepInEx.MelonLoader.Loader",
        authors=("1499501762",),
        repository="1499501762/BepInEx.MelonLoader.Loader",
        license="Apache-2.0",
        display_name={"en": "MLLoader"},
        description={"en": "bridge"},
        release={},
        dependencies=(),
        install={},
        category="utility",
        tags=(),
        kind="loaderbridge",
        provides={"lavagang.melonloader": "0.7.3"},
        supply={
            "melonloader:core": "{Sprocket}/MLLoader/MelonLoader",
            "melonloader:mod": "{Sprocket}/MLLoader/Mods",
            "melonloader:plugin": "{Sprocket}/MLLoader/Plugins",
            "melonloader:userlib": "{Sprocket}/MLLoader/UserLibs",
        },
    )


def v2_mod_package(
    package_id: str,
    file_name: str,
    content: bytes,
    *,
    file_type: str = "melonloader:mod",
    versions: tuple[str, ...] = ("1.0.0",),
) -> RegistryPackage:
    """v2 条目：安装规则给的是**类型**，目标目录由已装加载器的供给表决定。"""
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
                        f"https://github.com/fixture/FixtureMod/releases/download/v{version}/{file_name}"
                    ),
                    digest=f"sha256:{digest}",
                ),
            ),
        )
        for index, version in enumerate(versions, start=1)
    )
    return RegistryPackage(
        id=package_id,
        name="FixtureMod",
        authors=("test",),
        repository="fixture/FixtureMod",
        license="MIT",
        display_name={"en": package_id},
        description={"en": "test"},
        release={"assets": {"include": [file_name], "exclude": []}},
        dependencies=(),
        install={"exclude": []},
        category="utility",
        tags=(),
        schema_version=2,
        file_rules=({"match": file_name, "type": file_type},),
        releases=releases,
    )


class BridgeAdoptionTests(unittest.TestCase):
    """桥接加载器的目录也要被认领扫描到，认领的目标目录按同一张供给表解析。"""

    def game(self, root: Path) -> Path:
        game = root / "game"
        (game / "MLLoader" / "Mods").mkdir(parents=True)
        (game / "Mods").mkdir()
        (game / "Sprocket.exe").touch()
        return game

    def test_bridge_directories_are_scanned_for_local_dlls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            (game / "MLLoader" / "Mods" / "FixtureMod.dll").write_bytes(FIXTURE_MOD.read_bytes())
            (game / "Mods" / "Legacy.dll").write_bytes(b"legacy")

            found = list(
                ExistingModsAdopter._local_dlls(
                    Registry([bridge_package()]), game, set(), (BRIDGE_ID,)
                )
            )

            self.assertEqual([path.name for path in found], ["FixtureMod.dll"])

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_bridge_mod_is_adopted_by_its_declared_id(self, _running) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            (game / "MLLoader" / "Mods" / "FixtureMod.dll").write_bytes(FIXTURE_MOD.read_bytes())
            service = ModManagerService(root / "app")
            service.registry = Registry(
                [
                    bridge_package(),
                    v2_mod_package("fixture.sprocket-mod", "FixtureMod.dll", FIXTURE_MOD.read_bytes()),
                ]
            )
            service._installer_for(game).state_store.save(
                {"schema_version": 2, "packages": {BRIDGE_ID: {"name": "bridge", "files": []}}, "files": {}, "metadata": {}}
            )

            adopted = service.adopt_existing(game)

            self.assertEqual([record.package_id for record in adopted], ["fixture.sprocket-mod"])
            self.assertEqual(adopted[0].files, ("MLLoader/Mods/FixtureMod.dll",))


MELONLOADER_ID = "lavagang.melonloader"


def loader_releases(versions: tuple[str, ...]) -> tuple[ReleaseInfo, ...]:
    return tuple(
        ReleaseInfo(
            id=index,
            tag=f"v{version}",
            version=Version.parse(version),
            prerelease=False,
            published_at="",
            assets=(
                ReleaseAsset(
                    id=index,
                    name="MelonLoader.x64.zip",
                    size=1,
                    download_url=(
                        "https://github.com/LavaGang/MelonLoader/releases/download/"
                        f"v{version}/MelonLoader.x64.zip"
                    ),
                ),
            ),
        )
        for index, version in enumerate(versions, start=1)
    )


def melonloader_package(versions: tuple[str, ...] = ()) -> RegistryPackage:
    """原生 MelonLoader：与桥接加载器供给同一批类型，位置在游戏根目录。"""
    return RegistryPackage(
        id=MELONLOADER_ID,
        name="MelonLoader",
        authors=("LavaGang",),
        repository="LavaGang/MelonLoader",
        license="Apache-2.0",
        display_name={"en": "MelonLoader"},
        description={"en": "runtime"},
        release={},
        dependencies=(),
        install={},
        category="utility",
        tags=(),
        kind="modloader",
        supply={
            "melonloader:core": "{Sprocket}/MelonLoader",
            "melonloader:mod": "{Sprocket}/Mods",
            "melonloader:plugin": "{Sprocket}/Plugins",
            "melonloader:userlib": "{Sprocket}/UserLibs",
        },
        releases=loader_releases(versions) if versions else None,
    )


def detected_melonloader(game: Path) -> None:
    """磁盘上有真读得出版本的 MelonLoader 运行时，不进安装记录。"""
    (game / "version.dll").write_bytes(b"proxy")
    (game / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_MOD, game / "MelonLoader" / "net6" / "MelonLoader.dll")


class _NoGitHub:
    """认领加载器只读索引自带的发布数据：任何一次 GitHub 查询都让测试失败。"""

    def releases(self, package, refresh: bool = False):
        raise AssertionError(f"adoption must not query GitHub for {package.id}")

    def install_assets(self, package, release):
        raise AssertionError(f"adoption must not query GitHub for {package.id}")


class UnrecordedRuntimeAdoptionTests(unittest.TestCase):
    """记录为空、运行时只在磁盘上时，身份对上的模组与加载器都要被认领。"""

    def game(self, root: Path) -> Path:
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "UserLibs").mkdir()
        (game / "Sprocket.exe").touch()
        install_melonloader(game)
        return game

    def service(self, root: Path, packages: list[RegistryPackage]) -> ModManagerService:
        service = ModManagerService(root / "app")
        service.registry = Registry(packages)
        return service

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_two_suppliers_and_no_record_still_claims_by_declared_id(self, _running) -> None:
        # `melonloader:mod` 有原生与桥接两个供给者：记录为空时谁也定不下来，但检测到的运行时
        # 已经把目录说清楚了，认领必须照样发生。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            (game / "Mods" / "FixtureMod.dll").write_bytes(FIXTURE_MOD.read_bytes())
            service = self.service(
                root,
                [
                    bridge_package(),
                    melonloader_package(),
                    v2_mod_package("fixture.sprocket-mod", "FixtureMod.dll", FIXTURE_MOD.read_bytes()),
                ],
            )

            adopted = service.adopt_existing(game)

            claimed = {record.package_id: record for record in adopted}
            self.assertIn("fixture.sprocket-mod", claimed)
            self.assertEqual(claimed["fixture.sprocket-mod"].files, ("Mods/FixtureMod.dll",))

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_library_without_melonline_is_claimed_in_userlibs(self, _running) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            library = FIXTURE_DIR / "FixtureLibrary.dll"
            (game / "UserLibs" / "FixtureLibrary.dll").write_bytes(library.read_bytes())
            service = self.service(
                root,
                [
                    bridge_package(),
                    melonloader_package(),
                    v2_mod_package(
                        "fixture.sprocket-library",
                        "FixtureLibrary.dll",
                        library.read_bytes(),
                        file_type="melonloader:userlib",
                    ),
                ],
            )

            adopted = service.adopt_existing(game)

            claimed = {record.package_id: record for record in adopted}
            self.assertIn("fixture.sprocket-library", claimed)
            self.assertEqual(
                claimed["fixture.sprocket-library"].files, ("UserLibs/FixtureLibrary.dll",)
            )

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_four_part_internal_version_matches_the_three_part_tag(self, _running) -> None:
        # FixtureLibrary 没有 MelonInfo：版本来自程序集版本 3.0.0.0，必须命中三段标签 3.0.0，
        # 而不是退回最新发布。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            library = FIXTURE_DIR / "FixtureLibrary.dll"
            (game / "UserLibs" / "FixtureLibrary.dll").write_bytes(library.read_bytes())
            service = self.service(
                root,
                [
                    bridge_package(),
                    melonloader_package(),
                    v2_mod_package(
                        "fixture.sprocket-library",
                        "FixtureLibrary.dll",
                        library.read_bytes(),
                        file_type="melonloader:userlib",
                        versions=("3.1.0", "3.0.0"),
                    ),
                ],
            )

            adopted = service.adopt_existing(game)

            claimed = {record.package_id: record.version for record in adopted}
            self.assertEqual(claimed["fixture.sprocket-library"], "3.0.0")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_four_part_melonline_version_matches_the_three_part_tag(self, _running) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            (game / "Mods" / "FixtureMod.dll").write_bytes(FIXTURE_MOD.read_bytes())
            service = self.service(
                root,
                [
                    bridge_package(),
                    melonloader_package(),
                    v2_mod_package(
                        "fixture.sprocket-mod",
                        "FixtureMod.dll",
                        FIXTURE_MOD.read_bytes(),
                        versions=("0.2.5", "0.2.2"),
                    ),
                ],
            )
            metadata = replace(read_dll_metadata(FIXTURE_MOD), melon_version="0.2.2.0")

            with patch(
                "sprocket_mod_manager.application.adoption.read_cached_metadata",
                return_value=metadata,
            ):
                adopted = service.adopt_existing(game)

            claimed = {record.package_id: record.version for record in adopted}
            self.assertEqual(claimed["fixture.sprocket-mod"], "0.2.2")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_local_build_older_than_every_release_takes_the_newest(self, _running) -> None:
        # 自报版本哪个发布都对不上：按最新发布登记，包因此可管理（文件内容不动；
        # 完整性判定随后拿磁盘内容与全部发布版本的资产比对，对不上任何发布的构建会标成损坏）。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            library = FIXTURE_DIR / "FixtureLibrary.dll"
            (game / "UserLibs" / "FixtureLibrary.dll").write_bytes(library.read_bytes())
            service = self.service(
                root,
                [
                    bridge_package(),
                    melonloader_package(),
                    v2_mod_package(
                        "fixture.sprocket-library",
                        "FixtureLibrary.dll",
                        library.read_bytes(),
                        file_type="melonloader:userlib",
                        versions=("3.2.0", "3.1.0"),
                    ),
                ],
            )

            adopted = service.adopt_existing(game)

            claimed = {record.package_id: record.version for record in adopted}
            self.assertEqual(claimed["fixture.sprocket-library"], "3.2.0")


class DetectedLoaderAdoptionTests(unittest.TestCase):
    """磁盘上已在场、记录里没有的加载器：版本取检测到的真值，发布出处只查索引。"""

    def game(self, root: Path) -> Path:
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        detected_melonloader(game)
        return game

    def service(self, root: Path, packages: list[RegistryPackage]) -> ModManagerService:
        service = ModManagerService(root / "app")
        service.registry = Registry(packages)
        service.github = _NoGitHub()
        return service

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_detected_loader_is_adopted_with_its_detected_version(self, _running) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            service = self.service(root, [melonloader_package(("1.2.3", "1.2.0"))])

            adopted = service.adopt_existing(game)

            claimed = {record.package_id: record for record in adopted}
            self.assertIn(MELONLOADER_ID, claimed)
            self.assertEqual(claimed[MELONLOADER_ID].version, "1.2.3")
            self.assertEqual(claimed[MELONLOADER_ID].files, ("MelonLoader", "version.dll"))

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_the_adopted_loader_record_keeps_its_tree_and_proxy(self, _running) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            service = self.service(root, [melonloader_package(("1.2.3",))])

            service.adopt_existing(game)
            state = service._installer_for(game).state_store.load()
            record = state["packages"][MELONLOADER_ID]

            self.assertEqual(record["kind"], "modloader")
            self.assertEqual(record["directories"], ["MelonLoader"])
            self.assertEqual(
                [entry["path"] for entry in record["payload_files"]], ["version.dll"]
            )
            self.assertEqual(record["tag"], "v1.2.3")
            self.assertIn(
                MELONLOADER_ID, service.installed(game), "与磁盘对账后记录必须还在"
            )

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_a_detected_version_without_an_indexed_release_is_recorded_as_detected(self, _running) -> None:
        # 注册表没见过这个构建：如实记检测到的版本，不写发布出处，也不编资产。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            service = self.service(root, [melonloader_package(("9.9.9",))])

            adopted = service.adopt_existing(game)
            state = service._installer_for(game).state_store.load()
            record = state["packages"][MELONLOADER_ID]

            self.assertEqual({item.version for item in adopted}, {"1.2.3"})
            self.assertEqual(record["version"], "1.2.3")
            self.assertNotIn("tag", record)
            self.assertNotIn("release_id", record)
            self.assertEqual(record["assets"], [])

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_removing_an_adopted_loader_hands_its_tree_back(self, _running) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = self.game(root)
            service = self.service(root, [melonloader_package(("1.2.3",))])
            service.adopt_existing(game)

            removed, warnings = service.remove(MELONLOADER_ID, game)

            self.assertEqual(removed, [MELONLOADER_ID])
            self.assertEqual(warnings, [])
            self.assertFalse((game / "MelonLoader").exists())
            self.assertFalse((game / "version.dll").exists())


if __name__ == "__main__":
    unittest.main()
