"""补丁安装模式：替换别的包的文件，卸载时还原。

归档 `SprocketModManager/backup/patched/<相对路径>` 是"这个路径被补丁替换过"的唯一事实来源，
所以还原判据、归档清理都从它出发，不依赖安装记录里的额外字段。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_installer import registry_package  # noqa: E402

from sprocket_mod_manager.domain.errors import InstallConflictError, ScanError  # noqa: E402
from sprocket_mod_manager.domain.models import (  # noqa: E402
    MODLOADER_KIND,
    PreparedFile,
    PreparedPackage,
    PreparedPlan,
    RegistryPackage,
    ReleaseInfo,
    ResolvedPackage,
    ResolutionPlan,
)
from sprocket_mod_manager.domain.semver import Version  # noqa: E402
from sprocket_mod_manager.infrastructure.installer import Installer  # noqa: E402
from sprocket_mod_manager.infrastructure.scanner import PackageScanner  # noqa: E402
from sprocket_mod_manager.infrastructure.state import StateStore  # noqa: E402
from sprocket_mod_manager.utilities.checksums import sha256_file  # noqa: E402

LOADER_ID = "bepinex.bepinex-be"
PATCH_ID = "hans21223.sprocket-mod-loader"
BRIDGE_PATH = "BepInEx/core/Il2CppInterop.Runtime.dll"
ARCHIVE = Path("SprocketModManager") / "backup" / "patched"


def package(
        package_id: str,
        *,
        mode: str = "standard",
        dependencies: tuple[str, ...] = (),
        kind: str = "",
) -> RegistryPackage:
    base = registry_package(package_id)
    return replace(
        base,
        kind=kind or base.kind,
        install={"mode": mode},
        dependencies=tuple({"id": item, "version": "*", "when": "*"} for item in dependencies),
    )


def plan(root: Path, entry: RegistryPackage, files: list[tuple[str, bytes]], *, root_id: str = "") -> PreparedPlan:
    return combined_plan(root, [(entry, files)], root_id=root_id or entry.id)


def combined_plan(
    root: Path,
    entries: list[tuple[RegistryPackage, list[tuple[str, bytes]]]],
    *,
    root_id: str,
) -> PreparedPlan:
    prepared_packages: list[PreparedPackage] = []
    resolved_packages: list[ResolvedPackage] = []
    for index, (entry, files) in enumerate(entries):
        release = ReleaseInfo(index + 1, "v1.0.0", Version.parse("1.0.0"), False, "", ())
        resolved = ResolvedPackage(entry, release, tuple(dep["id"] for dep in entry.dependencies))
        prepared_files = []
        for file_index, (target, content) in enumerate(files):
            source = root / f"{entry.id}-{index}-{file_index}-{Path(target).name}"
            source.write_bytes(content)
            prepared_files.append(
                PreparedFile(entry.id, source, Path(target).name, target, sha256_file(source))
            )
        resolved_packages.append(resolved)
        prepared_packages.append(PreparedPackage(resolved, files=prepared_files))
    return PreparedPlan(ResolutionPlan(root_id, tuple(resolved_packages)), prepared_packages, root)


def game_dir(root: Path) -> Path:
    game = root / "game"
    (game / "BepInEx" / "core").mkdir(parents=True)
    (game / "Sprocket.exe").write_bytes(b"")
    return game


class PatchInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        running = patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
        running.start()
        self.addCleanup(running.stop)

    def installer_for(self, root: Path, store: StateStore) -> Installer:
        return Installer(root / "app", store)

    def install_loader(self, root: Path, game: Path, store: StateStore) -> Installer:
        installer = self.installer_for(root, store)
        installer.apply(plan(root, package(LOADER_ID), [(BRIDGE_PATH, b"loader bridge")]), game)
        self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"loader bridge")
        return installer

    def test_patch_overwrites_owned_file_and_archives_previous_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.install_loader(root, game, store)

            installer.apply(
                plan(
                    root,
                    package(PATCH_ID, mode="patch", dependencies=(LOADER_ID,)),
                    [(BRIDGE_PATH, b"patch bridge")],
                ),
                game,
            )

            self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"patch bridge")
            archive = game / ARCHIVE / "BepInEx" / "core" / "Il2CppInterop.Runtime.dll"
            self.assertTrue(archive.is_file(), "被替换的原件要归档")
            self.assertEqual(archive.read_bytes(), b"loader bridge")
            entry = store.load()["files"][BRIDGE_PATH]
            self.assertEqual(entry["owners"], [LOADER_ID, PATCH_ID])
            self.assertEqual(entry["sha256"], sha256_file(game / BRIDGE_PATH))

    def test_removing_the_patch_restores_the_archived_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.install_loader(root, game, store)
            installer.apply(
                plan(
                    root,
                    package(PATCH_ID, mode="patch", dependencies=(LOADER_ID,)),
                    [(BRIDGE_PATH, b"patch bridge")],
                ),
                game,
            )

            removed, warnings = installer.remove(PATCH_ID, game)

            self.assertEqual(removed, [PATCH_ID])
            self.assertEqual(warnings, [])
            self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"loader bridge")
            entry = store.load()["files"][BRIDGE_PATH]
            self.assertEqual(entry["owners"], [LOADER_ID])
            self.assertEqual(entry["sha256"], sha256_file(game / BRIDGE_PATH))
            self.assertFalse(
                (game / ARCHIVE / "BepInEx" / "core" / "Il2CppInterop.Runtime.dll").exists(),
                "补丁卸载后没有引用的归档要删掉",
            )

    def test_removing_the_patch_preserves_a_hand_modified_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.install_loader(root, game, store)
            installer.apply(
                plan(
                    root,
                    package(PATCH_ID, mode="patch", dependencies=(LOADER_ID,)),
                    [(BRIDGE_PATH, b"patch bridge")],
                ),
                game,
            )
            (game / BRIDGE_PATH).write_bytes(b"hand edit")

            _, warnings = installer.remove(PATCH_ID, game)

            self.assertEqual(warnings, [f"preserved modified file: {BRIDGE_PATH}"])
            self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"hand edit")
            self.assertEqual(store.load()["files"][BRIDGE_PATH]["owners"], [LOADER_ID])

    def test_patch_file_created_by_the_patch_is_deleted_on_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.installer_for(root, store)
            new_path = "BepInEx/core/PatchOnly.dll"
            installer.apply(
                plan(root, package(PATCH_ID, mode="patch"), [(new_path, b"patch only")]),
                game,
            )
            self.assertEqual((game / new_path).read_bytes(), b"patch only")

            removed, warnings = installer.remove(PATCH_ID, game)

            self.assertEqual(removed, [PATCH_ID])
            self.assertEqual(warnings, [])
            self.assertFalse((game / new_path).exists())
            self.assertFalse((game / ARCHIVE / "BepInEx" / "core" / "PatchOnly.dll").exists())

    def test_standard_package_still_conflicts_on_unmanaged_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.installer_for(root, store)
            target = game / "BepInEx" / "core" / "Unmanaged.dll"
            target.write_bytes(b"unmanaged")

            with self.assertRaises(InstallConflictError):
                installer.apply(
                    plan(root, package("test.mod"), [("BepInEx/core/Unmanaged.dll", b"managed")]),
                    game,
                )

            self.assertEqual(target.read_bytes(), b"unmanaged")

    def test_standard_package_still_conflicts_on_an_unmanaged_disabled_target(self) -> None:
        """禁用变体也是"已经存在、内容不同"的文件：不放行、也不吞掉它。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.installer_for(root, store)
            disabled = game / "Mods" / "Unmanaged.dll.disable"
            disabled.parent.mkdir(parents=True)
            disabled.write_bytes(b"unmanaged")

            with self.assertRaises(InstallConflictError):
                installer.apply(
                    plan(root, package("test.mod"), [("Mods/Unmanaged.dll", b"managed")]),
                    game,
                )

            self.assertEqual(disabled.read_bytes(), b"unmanaged")
            self.assertFalse((game / "Mods" / "Unmanaged.dll").exists())

    def test_patch_over_a_disabled_file_writes_it_in_place_and_archives_it(self) -> None:
        """补丁盖的是禁用的那个变体本身：原件归档、卸载还原，全程只有一份逻辑文件。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.installer_for(root, store)
            disabled = game / "Mods" / "Patched.dll.disable"
            disabled.parent.mkdir(parents=True)
            disabled.write_bytes(b"original")

            installer.apply(
                plan(root, package(PATCH_ID, mode="patch"), [("Mods/Patched.dll", b"patch content")]),
                game,
            )

            self.assertFalse((game / "Mods" / "Patched.dll").exists())
            self.assertEqual(disabled.read_bytes(), b"patch content")
            archive = game / ARCHIVE / "Mods" / "Patched.dll"
            self.assertEqual(archive.read_bytes(), b"original")

            installer.remove(PATCH_ID, game)

            self.assertEqual(disabled.read_bytes(), b"original")
            self.assertFalse(archive.exists())

    def test_patch_over_unmanaged_target_takes_it_over_and_archives_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.installer_for(root, store)
            target = game / "BepInEx" / "core" / "Unmanaged.dll"
            target.write_bytes(b"unmanaged")

            installer.apply(
                plan(root, package(PATCH_ID, mode="patch"), [("BepInEx/core/Unmanaged.dll", b"managed")]),
                game,
            )

            self.assertEqual(target.read_bytes(), b"managed")
            archive = game / ARCHIVE / "BepInEx" / "core" / "Unmanaged.dll"
            self.assertEqual(archive.read_bytes(), b"unmanaged")

    def test_removing_the_patch_restores_an_unmanaged_original(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.installer_for(root, store)
            target = game / "BepInEx" / "core" / "Unmanaged.dll"
            target.write_bytes(b"user supplied")
            installer.apply(
                plan(root, package(PATCH_ID, mode="patch"), [("BepInEx/core/Unmanaged.dll", b"patch content")]),
                game,
            )
            self.assertEqual(target.read_bytes(), b"patch content")

            removed, warnings = installer.remove(PATCH_ID, game)

            self.assertEqual(removed, [PATCH_ID])
            self.assertEqual(warnings, [])
            self.assertEqual(target.read_bytes(), b"user supplied", "不是补丁新建的文件必须还原，不能删掉")
            self.assertNotIn("BepInEx/core/Unmanaged.dll", store.load()["files"])

    def test_patch_and_loader_in_one_plan_in_either_order(self) -> None:
        for loader_first in (True, False):
            with self.subTest(loader_first=loader_first):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    game = game_dir(root)
                    store = StateStore(root / "app" / "installed.json")
                    installer = self.installer_for(root, store)
                    loader = package(LOADER_ID)
                    patch_package = package(PATCH_ID, mode="patch", dependencies=(LOADER_ID,))
                    entries = [
                        (loader, [(BRIDGE_PATH, b"loader bridge")]),
                        (patch_package, [(BRIDGE_PATH, b"patch bridge")]),
                    ]
                    if not loader_first:
                        entries.reverse()

                    installer.apply(combined_plan(root, entries, root_id=PATCH_ID), game)

                    self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"patch bridge")
                    archive = game / ARCHIVE / "BepInEx" / "core" / "Il2CppInterop.Runtime.dll"
                    self.assertEqual(archive.read_bytes(), b"loader bridge")
                    self.assertEqual(
                        store.load()["files"][BRIDGE_PATH]["owners"],
                        sorted([LOADER_ID, PATCH_ID]),
                    )

    def test_a_mod_bringing_the_patched_loader_along_keeps_the_patch(self) -> None:
        """另一个模组把它依赖的加载器带进计划时，加载器载荷不能把补丁的核心文件写回原件。

        加载器（`kind: modloader`）不记逐文件清单，所以它那份载荷落盘时看不到补丁的归属；
        没有这道保护，补丁会被静默换回原件，而安装记录里的摘要仍是补丁的内容。
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            loader = package(LOADER_ID, kind=MODLOADER_KIND)
            new_mod = package("test.qol")
            plugin_path = "BepInEx/plugins/QoL.dll"
            installer = self.installer_for(root, store)
            installer.apply(plan(root, loader, [(BRIDGE_PATH, b"loader bridge")]), game)
            installer.apply(
                plan(
                    root,
                    package(PATCH_ID, mode="patch", dependencies=(LOADER_ID,)),
                    [(BRIDGE_PATH, b"patch bridge")],
                ),
                game,
            )

            installer.apply(
                combined_plan(
                    root,
                    [
                        (loader, [(BRIDGE_PATH, b"loader bridge")]),
                        (new_mod, [(plugin_path, b"qol")]),
                    ],
                    root_id=new_mod.id,
                ),
                game,
            )

            self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"patch bridge")
            self.assertEqual((game / plugin_path).read_bytes(), b"qol")
            archive = game / ARCHIVE / "BepInEx" / "core" / "Il2CppInterop.Runtime.dll"
            self.assertEqual(archive.read_bytes(), b"loader bridge")
            self.assertEqual(
                store.load()["files"][BRIDGE_PATH]["sha256"],
                sha256_file(game / BRIDGE_PATH),
                "记录里的摘要必须与磁盘上补丁的内容一致",
            )

            removed, warnings = installer.remove(PATCH_ID, game)

            self.assertEqual(removed, [PATCH_ID])
            self.assertEqual(warnings, [])
            self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"loader bridge")
            self.assertFalse(archive.exists(), "补丁卸载后归档要清掉")

    def test_removing_one_of_two_patches_keeps_the_other(self) -> None:
        """两个补丁改同一条路径时，卸载其中一个不能把另一个的内容顶回原件。

        归档里只有最初那份原件（加载器的内容），所以后装的补丁被卸载时才轮到它还原。
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = game_dir(root)
            store = StateStore(root / "app" / "installed.json")
            installer = self.install_loader(root, game, store)
            first = package("test.patch-one", mode="patch")
            second = package("test.patch-two", mode="patch")
            installer.apply(plan(root, first, [(BRIDGE_PATH, b"one")]), game)
            installer.apply(plan(root, second, [(BRIDGE_PATH, b"two")]), game)
            archive = game / ARCHIVE / "BepInEx" / "core" / "Il2CppInterop.Runtime.dll"

            removed, warnings = installer.remove(first.id, game)

            self.assertEqual(removed, [first.id])
            self.assertEqual(warnings, [])
            self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"two")
            self.assertEqual(archive.read_bytes(), b"loader bridge")

            installer.remove(second.id, game)

            self.assertEqual((game / BRIDGE_PATH).read_bytes(), b"loader bridge")
            self.assertFalse(archive.exists())


class PatchScannerTests(unittest.TestCase):
    def patch_package(self) -> RegistryPackage:
        rule = {"match": "**", "type": "bepinex:core", "layout": "tree"}
        return replace(
            registry_package(PATCH_ID),
            schema_version=2,
            install={"mode": "patch", "files": [rule], "scan_dlls": False, "exclude": []},
            file_rules=(rule,),
        )

    def scanner(self) -> PackageScanner:
        return PackageScanner({"bepinex:core": PurePosixPath("BepInEx/core")})

    def test_patch_package_resolves_targets_like_standard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "patch.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("Il2CppInterop.Runtime.dll", b"bridge")

            files, ignored = self.scanner().scan(self.patch_package(), archive, root / "out")

            self.assertEqual([item.target for item in files], [BRIDGE_PATH])
            self.assertEqual(ignored, [])

    def test_patch_package_still_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "patch.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escape.dll", b"bridge")

            with self.assertRaises(ScanError):
                self.scanner().scan(self.patch_package(), archive, root / "out")
            self.assertFalse((root / "escape.dll").exists())


if __name__ == "__main__":
    unittest.main()
