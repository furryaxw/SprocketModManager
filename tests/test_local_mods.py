from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.application.adoption import (
    REASON_DECLARED_ID,
    REASON_REPOSITORY,
    match_package_by_metadata,
    normalize_repository,
)
from sprocket_mod_manager.application.local_mods import scan_local_mods, summarize
from sprocket_mod_manager.domain.models import RegistryPackage
from sprocket_mod_manager.infrastructure.dll_metadata import DllMetadata, read_dll_metadata

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dll_metadata" / "dll"
FIXTURE_MOD = FIXTURE_DIR / "FixtureMod.dll"
FIXTURE_PLUGIN = FIXTURE_DIR / "FixturePlugin.dll"
FIXTURE_LIBRARY = FIXTURE_DIR / "FixtureLibrary.dll"


def install_melonloader(game: Path) -> None:
    """游戏根目录的 MelonLoader 布局：`Mods` / `Plugins` / `UserLibs` 要被扫描就得先检测到它。"""
    (game / "version.dll").touch()
    (game / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    (game / "MelonLoader" / "net6" / "MelonLoader.dll").touch()


def make_package(
        package_id: str,
        name: str,
        authors: tuple[str, ...] = ("furryAxw",),
        display: dict[str, str] | None = None,
        description: dict[str, str] | None = None,
        repository: str | None = None,
) -> RegistryPackage:
    return RegistryPackage(
        id=package_id,
        name=name,
        authors=authors,
        repository=repository or f"furryaxw/{name}",
        license="GPL-3.0-only",
        display_name=display or {"en": name},
        description=description or {"en": f"{name} description"},
        release={},
        dependencies=(),
        install={},
        category="utility",
        tags=("fixture",),
    )


def metadata(**overrides: object) -> DllMetadata:
    values: dict[str, object] = {
        "path": r"C:\Game\Mods\Unknown.dll",
        "is_managed": True,
        "is_native": False,
        "assembly_name": "Unknown",
        "assembly_version": None,
        "file_version": None,
        "target_framework": None,
        "melon_kind": "Mods",
        "melon_name": "Unknown",
        "melon_version": None,
        "melon_author": None,
        "melon_download_link": None,
        "melon_credits": None,
        "sprocket": {},
        "errors": (),
    }
    values.update(overrides)
    return DllMetadata(**values)  # type: ignore[arg-type]


class MatchOrderTests(unittest.TestCase):
    """认领只用 DLL 内置身份：Sprocket.Mod.Id → Sprocket.Mod.Repository（然后才是 SHA-256）。"""

    def test_declared_id_wins(self) -> None:
        real = read_dll_metadata(FIXTURE_MOD)
        self.assertEqual(real.sprocket.get("id"), "fixture.sprocket-mod")

        packages = [make_package("fixture.sprocket-mod", "SomethingElse"), make_package("other.pkg", "FixtureMod")]
        match = match_package_by_metadata(real, packages)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.package_id, "fixture.sprocket-mod")
        self.assertEqual(match.reason, REASON_DECLARED_ID)

    def test_repository_matches_when_id_is_absent(self) -> None:
        candidate = metadata(path=r"C:\Game\Mods\CoolMod.dll", sprocket={"repository": "furryaxw/CoolMod"})
        packages = [make_package("furryaxw.cool-mod", "CoolMod", repository="furryaxw/CoolMod")]
        match = match_package_by_metadata(candidate, packages)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.reason, REASON_REPOSITORY)

    def test_repository_forms_are_normalized(self) -> None:
        self.assertEqual(normalize_repository("https://github.com/furryAxw/CoolMod.git"), "furryaxw/coolmod")
        self.assertEqual(normalize_repository("git@github.com:furryaxw/CoolMod"), "furryaxw/coolmod")
        self.assertEqual(normalize_repository("furryaxw/CoolMod/"), "furryaxw/coolmod")

        candidate = metadata(path=r"C:\Game\Mods\CoolMod.dll", sprocket={"repository": "https://github.com/furryAxw/CoolMod"})
        packages = [make_package("furryaxw.cool-mod", "CoolMod", repository="furryaxw/CoolMod")]
        match = match_package_by_metadata(candidate, packages)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.reason, REASON_REPOSITORY)

    def test_assembly_name_and_file_name_are_not_used(self) -> None:
        # 故意保留一个"名字巧合"的样本：不声明身份就不认领（回退交给 SHA-256）。
        candidate = metadata(path=r"C:\Game\Mods\CoolMod.dll", assembly_name="CoolMod", melon_author="furryAxw")
        packages = [make_package("furryaxw.cool-mod", "CoolMod", authors=("furryAxw",))]
        self.assertIsNone(match_package_by_metadata(candidate, packages))

        library = read_dll_metadata(FIXTURE_LIBRARY)
        self.assertIsNone(match_package_by_metadata(library, [make_package("furryaxw.fixture-library", "FixtureLibrary")]))

    def test_unmatched_returns_none(self) -> None:
        real = read_dll_metadata(FIXTURE_MOD)
        self.assertIsNone(match_package_by_metadata(real, [make_package("unrelated.pkg", "Unrelated")]))
        self.assertIsNone(match_package_by_metadata(real, []))


class LocalScanTests(unittest.TestCase):
    def _build_game_dir(self, root: Path) -> Path:
        (root / "Sprocket.exe").write_bytes(b"stub")
        install_melonloader(root)
        (root / "Mods").mkdir()
        (root / "Plugins").mkdir()
        (root / "UserLibs").mkdir()
        shutil.copyfile(FIXTURE_MOD, root / "Mods" / "FixtureMod.dll")
        shutil.copyfile(FIXTURE_MOD, root / "Mods" / "Disabled.dll.disable")
        shutil.copyfile(FIXTURE_PLUGIN, root / "Plugins" / "FixturePlugin.dll")
        shutil.copyfile(FIXTURE_LIBRARY, root / "UserLibs" / "FixtureLibrary.dll")
        (root / "Mods" / "Broken.dll").write_bytes(b"not a portable executable")
        return root

    def test_scan_reports_identity_state_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._build_game_dir(Path(directory))
            packages = [
                make_package("fixture.sprocket-mod", "FixtureMod", authors=("Fixture Author",), display={"en": "Fixture Mod"}),
                make_package("fixture.sprocket-plugin", "FixturePlugin", authors=("Fixture Author",)),
                make_package("furryaxw.fixture-library", "FixtureLibrary"),
            ]
            managed = {"mods/fixturemod.dll": "fixture.sprocket-mod"}

            registered = scan_local_mods(root, managed, packages, compute_hashes=True)
            mods = scan_local_mods(root, managed, packages)
            by_path = {mod.path: mod for mod in registered}
            self.assertEqual(len(mods), 5)
            self.assertEqual(len(by_path), 5)

            self.assertEqual(by_path["Mods/FixtureMod.dll"].display_name, "Fixture Mod")
            self.assertEqual(by_path["Mods/FixtureMod.dll"].version, "1.2.3")
            self.assertEqual(by_path["Mods/FixtureMod.dll"].authors, ("Fixture Author", "Second Author"))
            self.assertEqual(by_path["Mods/FixtureMod.dll"].kind, "Mods")
            self.assertEqual(by_path["Mods/FixtureMod.dll"].installed_package_id, "fixture.sprocket-mod")
            self.assertEqual(by_path["Mods/FixtureMod.dll"].registry_match, REASON_DECLARED_ID)
            self.assertEqual(by_path["Mods/FixtureMod.dll"].registry_display_name.get("en"), "Fixture Mod")
            self.assertEqual(len(by_path["Mods/FixtureMod.dll"].sha256), 64)
            self.assertEqual(by_path["Mods/FixtureMod.dll"].required_dependencies, ("SprocketDepth", "SprocketModAPI"))
            self.assertEqual(by_path["Mods/FixtureMod.dll"].incompatible_assemblies, ("LegacyOverhaul",))
            payload = by_path["Mods/FixtureMod.dll"].as_dict()
            self.assertEqual(payload["required_dependencies"], ["SprocketDepth", "SprocketModAPI"])
            self.assertEqual(payload["incompatible_assemblies"], ["LegacyOverhaul"])
            self.assertEqual(payload["missing_dependencies"], ["SprocketDepth", "SprocketModAPI"],
                             "临时目录里没有这两个程序集，缺口来自 DLL 元数据而非 installed.json")

            disabled = by_path["Mods/Disabled.dll.disable"]
            self.assertTrue(disabled.disabled)
            self.assertEqual(disabled.display_name, "Fixture Mod")
            self.assertEqual(disabled.registry_id, "fixture.sprocket-mod")
            self.assertEqual(disabled.installed_package_id, "")

            plugin = by_path["Plugins/FixturePlugin.dll"]
            self.assertEqual(plugin.kind, "Plugins")
            self.assertEqual(plugin.version, "2.5.1")

            library = by_path["UserLibs/FixtureLibrary.dll"]
            self.assertEqual(library.kind, "UserLibs")
            self.assertEqual(library.registry_match, "", "a library without declared identity is not claimed")

            broken = by_path["Mods/Broken.dll"]
            self.assertNotEqual(broken.error, "")
            self.assertEqual(broken.display_name, "Broken.dll")

            # 列表刷新默认不算哈希（大程序集会让整页变慢），需要的调用方显式开启。
            self.assertEqual(
                {mod.sha256 for mod in mods if mod.sha256},
                set(),
                "the default scan must not compute hashes",
            )

    def test_summary_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._build_game_dir(Path(directory))
            mods = scan_local_mods(root, {}, [make_package("fixture.sprocket-mod", "FixtureMod", authors=("Fixture Author",))])
            summary = summarize(mods)
            self.assertEqual(summary["total"], 5)
            self.assertEqual(summary["disabled"], 1)
            self.assertEqual(summary["unmanaged"], 5)
            self.assertEqual(summary["unreadable"], 1)
            self.assertEqual(summary["registry_matched"], 2)
            self.assertEqual(summary["missing_dependencies"], 2,
                             "FixtureMod 与它的禁用副本都声明了缺失依赖")

    def test_disabled_entries_sort_after_enabled_ones_in_their_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._build_game_dir(Path(directory))
            paths = [mod.path for mod in scan_local_mods(root, {})]
            self.assertLess(paths.index("Mods/FixtureMod.dll"), paths.index("Mods/Disabled.dll.disable"))

    def test_each_root_forms_its_own_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._build_game_dir(Path(directory))
            paths = [mod.path for mod in scan_local_mods(root, {})]
            self.assertEqual(
                [path.split("/", 1)[0] for path in paths],
                ["Mods", "Mods", "Mods", "Plugins", "UserLibs"],
                "Mods、Plugins、UserLibs 各自成段，库排在最后",
            )

    def test_empty_game_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mods = scan_local_mods(root, {})
            self.assertEqual(mods, [])
            self.assertEqual(summarize(mods)["total"], 0)

    def test_scan_ignores_dlls_below_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._build_game_dir(Path(directory))
            nested = root / "Mods" / "Nested"
            nested.mkdir()
            shutil.copyfile(FIXTURE_MOD, nested / "Nested.dll")
            shutil.copyfile(FIXTURE_MOD, nested / "Nested.dll.disable")

            paths = [mod.path for mod in scan_local_mods(root, {})]

            self.assertNotIn("Mods/Nested/Nested.dll", paths)
            self.assertNotIn("Mods/Nested/Nested.dll.disable", paths)
            self.assertEqual(len(paths), 5)


class LocalModsApiTests(unittest.TestCase):
    """通过 ClientApi 验证接线：清单端点 + 启用/禁用端点（含路径安全）。"""

    def _api(self, root: Path) -> tuple[object, Path]:
        from sprocket_mod_manager.application.service import ModManagerService
        from sprocket_mod_manager.domain.registry import Registry
        from sprocket_mod_manager.infrastructure.config import ConfigStore
        from sprocket_mod_manager.presentation.web_gui import ClientApi

        app_dir = root / "app"
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Plugins").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        install_melonloader(game)
        shutil.copyfile(FIXTURE_MOD, game / "Mods" / "FixtureMod.dll")
        shutil.copyfile(FIXTURE_PLUGIN, game / "Plugins" / "FixturePlugin.dll")
        ConfigStore(app_dir).save({"language": "en", "game_path": str(game), "index_url": ""})

        service = ModManagerService(app_dir)
        service.registry = Registry([make_package("fixture.sprocket-mod", "FixtureMod", authors=("Fixture Author",))])
        api = ClientApi("0.3.3", app_dir=app_dir, service_factory=lambda _app_dir: service)
        return api, game

    def test_local_mods_endpoint_reports_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, _game = self._api(Path(directory))
            try:
                result = api.get_local_mods()
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            paths = {mod["path"]: mod for mod in result["mods"]}
            self.assertEqual(set(paths), {"Mods/FixtureMod.dll", "Plugins/FixturePlugin.dll"})
            self.assertEqual(paths["Mods/FixtureMod.dll"]["display_name"], "Fixture Mod")
            self.assertEqual(paths["Mods/FixtureMod.dll"]["registry_id"], "fixture.sprocket-mod")
            self.assertEqual(paths["Plugins/FixturePlugin.dll"]["kind"], "Plugins")
            self.assertEqual(result["summary"]["total"], 2)
            self.assertEqual(result["summary"]["registry_matched"], 1)

    def test_toggle_endpoint_renames_and_reports_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game = self._api(Path(directory))
            try:
                disabled = api.toggle_mod("Mods/FixtureMod.dll", False)
                self.assertTrue(disabled["ok"], disabled)
                self.assertEqual(disabled["toggled"], "Mods/FixtureMod.dll.disable")
                self.assertTrue(disabled["restart_required"])
                self.assertFalse((game / "Mods" / "FixtureMod.dll").exists())
                self.assertTrue((game / "Mods" / "FixtureMod.dll.disable").is_file())
                listed = api.get_local_mods()
                self.assertIn("Mods/FixtureMod.dll.disable", {mod["path"] for mod in listed["mods"]})
                self.assertEqual(listed["summary"]["disabled"], 1)

                enabled = api.toggle_mod("Mods/FixtureMod.dll.disable", True)
                self.assertTrue(enabled["ok"], enabled)
                self.assertTrue((game / "Mods" / "FixtureMod.dll").is_file())
            finally:
                api.install_queue.close()

    def test_toggle_endpoint_rejects_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game = self._api(Path(directory))
            (game / "UserData").mkdir(exist_ok=True)
            (game / "UserData" / "thing.dll").write_bytes(b"not a real assembly")
            (game / "Mods" / "FixtureMod.txt").write_text("x", encoding="utf-8")
            (Path(directory) / "outside.dll").write_bytes(b"outside")
            try:
                for path in ("../outside.dll", "Mods/../outside.dll", "UserData/thing.dll", "Mods/FixtureMod.txt", ""):
                    result = api.toggle_mod(path, False)
                    self.assertFalse(result["ok"], f"{path!r} should be rejected")
                    self.assertEqual(result.get("code"), "toggle_mod_failed")
            finally:
                api.install_queue.close()
            self.assertTrue((Path(directory) / "outside.dll").is_file())
            self.assertTrue((game / "UserData" / "thing.dll").is_file())

    def test_open_mod_location_reveals_a_file_inside_the_game_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game = self._api(Path(directory))
            try:
                with patch(
                    "sprocket_mod_manager.presentation.controllers.catalog_controller.reveal_in_file_manager"
                ) as reveal:
                    result = api.open_mod_location("Mods/FixtureMod.dll")
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["path"], "Mods/FixtureMod.dll")
        reveal.assert_called_once_with(game / "Mods" / "FixtureMod.dll")

    def test_open_mod_location_rejects_paths_outside_the_game_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, _game = self._api(Path(directory))
            try:
                with patch(
                    "sprocket_mod_manager.presentation.controllers.catalog_controller.reveal_in_file_manager"
                ) as reveal:
                    results = [
                        api.open_mod_location(path)
                        for path in ("../outside.dll", "C:/Windows/System32/calc.exe", "")
                    ]
            finally:
                api.install_queue.close()

        for result in results:
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["code"], "open_mod_location_failed")
        reveal.assert_not_called()


BRIDGE_ID = "1499501762.bepinex-melonloader-loader"
BEPINEX_ID = "bepinex.bepinex-be"


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
        provides={"bepinex.bepinex": "{version}"},
    )


class RuntimeDirectoryTests(unittest.TestCase):
    """目录跟着已装的加载器走：模组住在 `MLLoader/Mods` 时扫描也跟着去那里。"""

    def _game(self, root: Path) -> Path:
        (root / "Sprocket.exe").write_bytes(b"stub")
        install_melonloader(root)
        (root / "Mods").mkdir()
        (root / "MLLoader" / "Mods").mkdir(parents=True)
        (root / "BepInEx" / "plugins").mkdir(parents=True)
        shutil.copyfile(FIXTURE_MOD, root / "Mods" / "LegacyMod.dll")
        shutil.copyfile(FIXTURE_MOD, root / "MLLoader" / "Mods" / "BridgeMod.dll")
        (root / "BepInEx" / "plugins" / "BepInExPlugin.dll").write_bytes(FIXTURE_MOD.read_bytes())
        return root

    def test_a_bridge_mod_is_listed_where_its_loader_puts_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._game(Path(directory))
            packages = [
                bridge_package(),
                make_package("fixture.sprocket-mod", "FixtureMod", authors=("Fixture Author",)),
            ]
            mods = scan_local_mods(root, {}, packages, installed=(BRIDGE_ID,))
            by_path = {mod.path: mod for mod in mods}

            self.assertEqual(set(by_path), {"MLLoader/Mods/BridgeMod.dll"})
            entry = by_path["MLLoader/Mods/BridgeMod.dll"]
            self.assertEqual(entry.kind, "Mods")
            self.assertEqual(entry.registry_id, "fixture.sprocket-mod")
            self.assertEqual(entry.display_name, "Fixture Mod")

    def test_the_bridge_loader_itself_is_not_a_mod(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._game(Path(directory))
            (root / "MLLoader" / "MelonLoader").mkdir(parents=True)
            shutil.copyfile(FIXTURE_MOD, root / "MLLoader" / "MelonLoader" / "MelonLoader.dll")

            paths = {mod.path for mod in scan_local_mods(root, {}, [bridge_package()], installed=(BRIDGE_ID,))}

            self.assertNotIn("MLLoader/MelonLoader/MelonLoader.dll", paths)

    def test_bepinex_plugins_are_listed_without_a_fabricated_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._game(Path(directory))
            mods = scan_local_mods(root, {}, [bepinex_package()], installed=(BEPINEX_ID,))
            by_path = {mod.path: mod for mod in mods}

            entry = by_path["BepInEx/plugins/BepInExPlugin.dll"]
            self.assertEqual(entry.kind, "BepInEx plugins")
            self.assertEqual(entry.display_name, "BepInExPlugin.dll")
            self.assertEqual(entry.version, "")
            self.assertEqual(entry.authors, ())
            self.assertEqual(entry.declared_id, "")
            self.assertEqual(entry.registry_id, "")
            self.assertEqual(entry.assembly_name, "")
            self.assertEqual(entry.error, "")
            self.assertIn("Mods/LegacyMod.dll", by_path, "传统目录照常扫描")


class BridgeRuntimeApiTests(unittest.TestCase):
    """桥接加载器装好后，启用/禁用要认它安家的目录。"""

    def _api(self, root: Path) -> tuple[object, Path]:
        from sprocket_mod_manager.application.service import ModManagerService
        from sprocket_mod_manager.domain.registry import Registry
        from sprocket_mod_manager.infrastructure.config import ConfigStore
        from sprocket_mod_manager.infrastructure.manager_paths import state_file_path
        from sprocket_mod_manager.infrastructure.state import StateStore
        from sprocket_mod_manager.presentation.web_gui import ClientApi

        app_dir = root / "app"
        game = root / "game"
        (game / "MLLoader" / "Mods").mkdir(parents=True)
        (game / "BepInEx" / "plugins").mkdir(parents=True)
        (game / "Mods").mkdir()
        (game / "Sprocket.exe").touch()
        (game / "BepInEx" / "plugins" / "BepInEx.MelonLoader.Loader.dll").write_bytes(b"bridge")
        shutil.copyfile(FIXTURE_MOD, game / "MLLoader" / "Mods" / "BridgeMod.dll")
        shutil.copyfile(FIXTURE_MOD, game / "Mods" / "LegacyMod.dll")
        ConfigStore(app_dir).save({"language": "en", "game_path": str(game), "index_url": ""})

        service = ModManagerService(app_dir)
        service.registry = Registry([bridge_package()])
        StateStore(state_file_path(game)).save(
            {
                "schema_version": 2,
                "packages": {
                    BRIDGE_ID: {
                        "name": "BepInEx.MelonLoader.Loader",
                        "files": ["BepInEx/plugins/BepInEx.MelonLoader.Loader.dll"],
                    }
                },
                "files": {},
                "metadata": {},
            }
        )
        api = ClientApi("0.3.3", app_dir=app_dir, service_factory=lambda _app_dir: service)
        return api, game

    def test_bridge_mods_are_listed_and_toggleable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game = self._api(Path(directory))
            try:
                listed = api.get_local_mods()
                self.assertTrue(listed["ok"], listed)
                self.assertEqual(
                    {mod["path"] for mod in listed["mods"]},
                    {"MLLoader/Mods/BridgeMod.dll"},
                    "桥接加载器决定模组住哪里，游戏根的 Mods 不再是它读的目录",
                )

                disabled = api.toggle_mod("MLLoader/Mods/BridgeMod.dll", False)
                self.assertTrue(disabled["ok"], disabled)
                self.assertEqual(disabled["toggled"], "MLLoader/Mods/BridgeMod.dll.disable")
                self.assertTrue((game / "MLLoader" / "Mods" / "BridgeMod.dll.disable").is_file())

                enabled = api.toggle_mod("MLLoader/Mods/BridgeMod.dll.disable", True)
                self.assertTrue(enabled["ok"], enabled)
                self.assertTrue((game / "MLLoader" / "Mods" / "BridgeMod.dll").is_file())
            finally:
                api.install_queue.close()

    def test_bepinex_files_are_not_toggleable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game = self._api(Path(directory))
            try:
                result = api.toggle_mod("BepInEx/plugins/BepInEx.MelonLoader.Loader.dll", False)
            finally:
                api.install_queue.close()

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["code"], "toggle_mod_failed")
            self.assertTrue((game / "BepInEx" / "plugins" / "BepInEx.MelonLoader.Loader.dll").is_file())


class DetectedRuntimeTests(unittest.TestCase):
    """激活看磁盘上有没有运行时，不看安装记录：管理器之外装上的 MelonLoader 照样激活。"""

    def test_an_unrecorded_but_detected_melonloader_still_lists_its_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Sprocket.exe").write_bytes(b"stub")
            install_melonloader(root)
            (root / "Mods").mkdir()
            (root / "UserLibs").mkdir()
            shutil.copyfile(FIXTURE_MOD, root / "Mods" / "FixtureMod.dll")
            shutil.copyfile(FIXTURE_LIBRARY, root / "UserLibs" / "FixtureLibrary.dll")

            mods = scan_local_mods(root, {}, ())
            self.assertEqual(
                {mod.path for mod in mods},
                {"Mods/FixtureMod.dll", "UserLibs/FixtureLibrary.dll"},
            )

    def test_nothing_is_listed_without_a_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Sprocket.exe").write_bytes(b"stub")
            (root / "Mods").mkdir()
            shutil.copyfile(FIXTURE_MOD, root / "Mods" / "FixtureMod.dll")

            self.assertEqual(scan_local_mods(root, {}, ()), [])


if __name__ == "__main__":
    unittest.main()
