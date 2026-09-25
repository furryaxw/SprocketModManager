"""标识符：运行时检测决定激活，目录来自已装供给者的供给表，壳不认任何身份。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.application.identifiers import (
    BEPINEX_CAPABILITY,
    MELONLOADER_CAPABILITY,
    ModDirectory,
    ModType,
    active_identifiers,
    detected_capabilities,
    mod_directory_paths,
    scan_targets,
    toggle_directories,
)
from sprocket_mod_manager.application.identifiers.bepinex import BepInExIdentifier
from sprocket_mod_manager.application.identifiers.melonloader import MelonLoaderIdentifier
from sprocket_mod_manager.domain.models import RegistryPackage

FIXTURE_MOD = (
    Path(__file__).resolve().parent / "fixtures" / "dll_metadata" / "dll" / "FixtureMod.dll"
)

BRIDGE_ID = "1499501762.bepinex-melonloader-loader"
BEPINEX_ID = "bepinex.bepinex-be"


def melonloader_game(root: Path) -> Path:
    """游戏根目录装了 MelonLoader：`version.dll` 代理 + net6 运行时 DLL。"""
    (root / "version.dll").write_bytes(b"proxy")
    (root / "MelonLoader" / "net6").mkdir(parents=True)
    shutil.copyfile(FIXTURE_MOD, root / "MelonLoader" / "net6" / "MelonLoader.dll")
    return root


def bridged_game(root: Path) -> Path:
    (root / "MLLoader" / "MelonLoader" / "net6").mkdir(parents=True)
    shutil.copyfile(FIXTURE_MOD, root / "MLLoader" / "MelonLoader" / "net6" / "MelonLoader.dll")
    return root


def bepinex_game(root: Path) -> Path:
    (root / "winhttp.dll").write_bytes(b"proxy")
    (root / "BepInEx" / "core").mkdir(parents=True)
    shutil.copyfile(FIXTURE_MOD, root / "BepInEx" / "core" / "BepInEx.Core.dll")
    return root


def loader_package(
        package_id: str,
        supply: dict[str, str],
        *,
        provides: dict[str, str] | None = None,
        kind: str = "modloader",
) -> RegistryPackage:
    return RegistryPackage(
        id=package_id,
        name=package_id,
        authors=("test",),
        repository=f"test/{package_id}",
        license="MIT",
        display_name={"en": package_id},
        description={"en": package_id},
        release={},
        dependencies=(),
        install={},
        category="utility",
        tags=(),
        kind=kind,
        supply=dict(supply),
        provides=dict(provides or {}),
    )


def bridge_package() -> RegistryPackage:
    return loader_package(
        BRIDGE_ID,
        {
            "melonloader:core": "{Sprocket}/MLLoader/MelonLoader",
            "melonloader:mod": "{Sprocket}/MLLoader/Mods",
            "melonloader:plugin": "{Sprocket}/MLLoader/Plugins",
            "melonloader:userlib": "{Sprocket}/MLLoader/UserLibs",
        },
        provides={"lavagang.melonloader": "0.7.3"},
        kind="loaderbridge",
    )


def bepinex_package() -> RegistryPackage:
    return loader_package(
        BEPINEX_ID,
        {
            "bepinex:core": "{Sprocket}/BepInEx/core",
            "bepinex:plugin": "{Sprocket}/BepInEx/plugins",
            "bepinex:patchers": "{Sprocket}/BepInEx/patchers",
        },
        provides={BEPINEX_CAPABILITY: "{version}"},
    )


class DirectoryResolutionTests(unittest.TestCase):
    def test_a_detected_melonloader_reads_its_root_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = melonloader_game(Path(directory))
            self.assertEqual(
                mod_directory_paths(game, []),
                ("Mods", "Plugins", "UserLibs"),
            )
            self.assertEqual(toggle_directories(game, []), ("Mods", "Plugins"))

    def test_a_detected_bridge_reads_the_mlloader_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = bridged_game(Path(directory))
            self.assertEqual(
                mod_directory_paths(game, []),
                ("MLLoader/Mods", "MLLoader/Plugins", "MLLoader/UserLibs"),
            )
            self.assertEqual(
                toggle_directories(game, []),
                ("MLLoader/Mods", "MLLoader/Plugins"),
                "用户库不参与启用/禁用",
            )

    def test_an_installed_bridge_supply_moves_the_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            (game / "MLLoader").mkdir()
            paths = mod_directory_paths(game, [bridge_package()], (BRIDGE_ID,))
            self.assertEqual(
                paths,
                ("MLLoader/Mods", "MLLoader/Plugins", "MLLoader/UserLibs"),
                "运行时的 core 目录不是模组目录，不该进清单",
            )

    def test_an_uninstalled_provider_does_not_move_the_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = melonloader_game(Path(directory))
            self.assertEqual(
                mod_directory_paths(game, [bridge_package()]),
                ("Mods", "Plugins", "UserLibs"),
            )

    def test_the_native_loader_keeps_the_game_root_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            native = loader_package(
                "lavagang.melonloader",
                {
                    "melonloader:core": "{Sprocket}/MelonLoader",
                    "melonloader:mod": "{Sprocket}/Mods",
                    "melonloader:plugin": "{Sprocket}/Plugins",
                    "melonloader:userlib": "{Sprocket}/UserLibs",
                },
            )
            self.assertEqual(
                mod_directory_paths(game, [native], ("lavagang.melonloader",)),
                ("Mods", "Plugins", "UserLibs"),
            )

    def test_a_detected_bepinex_declares_its_plugin_and_patcher_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = bepinex_game(Path(directory))
            self.assertEqual(
                mod_directory_paths(game, []),
                ("BepInEx/plugins", "BepInEx/patchers"),
            )

    def test_the_same_directory_is_only_read_once(self) -> None:
        first = loader_package("bridge.one", {"melonloader:mod": "{Sprocket}/Mods"})
        second = loader_package("bridge.two", {"melonloader:mod": "{Sprocket}/Mods"})
        with tempfile.TemporaryDirectory() as directory:
            paths = mod_directory_paths(
                Path(directory), [first, second], ("bridge.one", "bridge.two")
            )
        self.assertEqual(paths, ("Mods",))

    def test_scan_targets_pair_each_directory_with_its_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = melonloader_game(Path(directory))
            targets = scan_targets(game, [])
        self.assertEqual(
            [(type(identifier).__name__, directory.path) for identifier, directory in targets],
            [
                ("MelonLoaderIdentifier", "Mods"),
                ("MelonLoaderIdentifier", "Plugins"),
                ("MelonLoaderIdentifier", "UserLibs"),
            ],
        )


class ActivationTests(unittest.TestCase):
    def test_nothing_present_activates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            self.assertEqual(active_identifiers(game, []), ())
            self.assertEqual(mod_directory_paths(game, []), ())
            self.assertEqual(toggle_directories(game, []), ())

    def test_a_detected_melonloader_activates_its_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = melonloader_game(Path(directory))
            active = active_identifiers(game, [])
        self.assertEqual([type(identifier).__name__ for identifier in active], ["MelonLoaderIdentifier"])

    def test_a_detected_bridge_activates_melonloader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = bridged_game(Path(directory))
            active = active_identifiers(game, [])
        self.assertEqual([type(identifier).__name__ for identifier in active], ["MelonLoaderIdentifier"])

    def test_an_installed_provider_activates_its_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active = active_identifiers(Path(directory), [bepinex_package()], (BEPINEX_ID,))
        self.assertEqual([type(identifier).__name__ for identifier in active], ["BepInExIdentifier"])

    def test_a_capability_activates_its_identifier_without_a_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            active = active_identifiers(
                Path(directory), [], (), {BEPINEX_CAPABILITY: "6.0.0"}
            )
        self.assertEqual([type(identifier).__name__ for identifier in active], ["BepInExIdentifier"])

    def test_detected_capabilities_carry_the_runtime_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = melonloader_game(Path(directory))
            detected = detected_capabilities(game)
        self.assertEqual(detected, {MELONLOADER_CAPABILITY: "1.2.3"})

    def test_nothing_detected_is_an_empty_capability_map(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(detected_capabilities(Path(directory)), {})
        self.assertEqual(detected_capabilities(None), {})

    def test_a_detected_runtime_without_a_readable_version_still_activates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            (game / "version.dll").touch()
            (game / "MelonLoader" / "net6").mkdir(parents=True)
            (game / "MelonLoader" / "net6" / "MelonLoader.dll").write_bytes(b"not a PE")

            self.assertEqual(
                [type(identifier).__name__ for identifier in active_identifiers(game, [])],
                ["MelonLoaderIdentifier"],
            )
            self.assertEqual(mod_directory_paths(game, []), ("Mods", "Plugins", "UserLibs"))
            self.assertEqual(detected_capabilities(game), {}, "版本未知时不记空版本")


class IdentifyTests(unittest.TestCase):
    def test_melonloader_identifies_the_assemblies_in_its_directories(self) -> None:
        metadata = MelonLoaderIdentifier().identify(FIXTURE_MOD)
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata.sprocket.get("id"), "fixture.sprocket-mod")

    def test_bepinex_identifies_nothing(self) -> None:
        self.assertIsNone(BepInExIdentifier().identify(FIXTURE_MOD))

    def test_a_directory_carries_its_type(self) -> None:
        directory = ModDirectory(
            "MLLoader/Mods",
            ModType(id="melonloader:mod", directory="Mods", kind="Mods", toggleable=True),
        )
        self.assertEqual(directory.type.kind, "Mods")
        self.assertTrue(directory.type.toggleable)


if __name__ == "__main__":
    unittest.main()
