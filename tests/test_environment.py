"""本机环境（Sprocket 版本 + 各加载器版本）的读取与展示契约。"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.application.identifiers import MELONLOADER_CAPABILITY
from sprocket_mod_manager.application.service import ModManagerService
from sprocket_mod_manager.domain.compatibility import COMPATIBLE
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.domain.semver import Version
from sprocket_mod_manager.infrastructure.config import ConfigStore
from sprocket_mod_manager.infrastructure.game_version import (
    STATE_LEGACY,
    STATE_OK,
    STATE_UNCONFIGURED,
    STATE_UNREADABLE,
    read_game_version,
)
from sprocket_mod_manager.domain.models import (
    PreparedFile,
    PreparedPackage,
    PreparedPlan,
    ReleaseAsset,
    ReleaseInfo,
    RegistryPackage,
    ResolvedPackage,
    ResolutionPlan,
)
from sprocket_mod_manager.infrastructure.manager_paths import state_file_path
from sprocket_mod_manager.infrastructure.state import StateStore
from sprocket_mod_manager.presentation.web_gui import ClientApi
from sprocket_mod_manager.utilities.checksums import sha256_file

ROOT = Path(__file__).resolve().parents[1]
CLIENT_UI = ROOT / "sprocket_mod_manager" / "presentation" / "client_ui"
FIXTURE_MOD = ROOT / "tests" / "fixtures" / "dll_metadata" / "dll" / "FixtureMod.dll"
LOADER_ID = "lavagang.melonloader"
DETECTED_LOADER_VERSION = "1.2.3"


def game_dir_with_version(root: Path, payload: bytes) -> Path:
    """造一个带 `Sprocket_Data/globalgamemanagers` 的游戏目录（版本串按 Unity 的写法嵌进去）。"""
    game = root / "game"
    (game / "Sprocket_Data").mkdir(parents=True, exist_ok=True)
    (game / "Sprocket.exe").touch()
    (game / "Sprocket_Data" / "globalgamemanagers").write_bytes(payload)
    return game


def unity_payload(version: str) -> bytes:
    # 真实文件里是「Unity 版本串 + 长度前缀 + 游戏版本串」，这里照着这个形状造。
    return b"2022.3.62f2\x00" + b"\x08\x00\x00\x00" + version.encode() + b"\x01\x00\x00\x00"


def _index_file(root: Path, *, sprocket_range: str = "<0.2.54.0") -> Path:
    """一个能过 Registry 校验的最小索引，顶层带着供给表（注册表那份的来源）。"""
    path = root / "index.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game": {"id": "hamish.sprocket", "name": "Sprocket"},
                "providers": {
                    "schema_version": 2,
                    "entries": [{"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": sprocket_range}],
                },
                "packages": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def loader_package() -> RegistryPackage:
    """一个带供给表和内嵌 release 的加载器包：不用联网就能查最新版。"""
    release = ReleaseInfo(
        id=1,
        tag="v0.7.3",
        version=Version(0, 7, 3),
        prerelease=False,
        published_at="",
        assets=(
            ReleaseAsset(
                id=1,
                name="MelonLoader.x64.zip",
                size=1,
                download_url=(
                    "https://github.com/LavaGang/MelonLoader/releases/download/v0.7.3/MelonLoader.x64.zip"
                ),
            ),
        ),
        page_url="https://github.com/LavaGang/MelonLoader/releases/tag/v0.7.3",
    )
    return RegistryPackage(
        id=LOADER_ID,
        name="MelonLoader",
        authors=("LavaGang",),
        repository="LavaGang/MelonLoader",
        license="Apache-2.0",
        display_name={"en": "MelonLoader"},
        description={"en": "mod loader"},
        release={"version_pattern": r"^v?(\d+\.\d+\.\d+)$", "assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=(),
        install={},
        category="loader",
        tags=(),
        kind="modloader",
        supply={"melonloader:mod": "{Sprocket}/Mods", "melonloader:plugin": "{Sprocket}/Plugins"},
        releases=(release,),
    )


BEPINEX_ID = "bepinex.bepinex-be"


def bepinex_package() -> RegistryPackage:
    """另一个基础运行时：与官方 MelonLoader 抢同一个引导槽，但不共享能力。"""
    release = ReleaseInfo(
        id=2,
        tag="6.0.0-be.788",
        version=Version.parse("6.0.0-be.788"),
        prerelease=True,
        published_at="",
        assets=(
            ReleaseAsset(
                id=2,
                name="BepInEx-Unity.IL2CPP-win-x64-6.0.0-be.788.zip",
                size=1,
                download_url="https://builds.bepinex.dev/projects/bepinex_be/788/BepInEx.zip",
            ),
        ),
        page_url="https://builds.bepinex.dev/projects/bepinex_be",
    )
    return RegistryPackage(
        id=BEPINEX_ID,
        name="BepInEx",
        authors=("BepInEx",),
        repository="BepInEx/BepInEx",
        license="LGPL-2.1-only",
        display_name={"en": "BepInEx (Bleeding Edge)"},
        description={"en": "runtime"},
        release={"version_pattern": r"^v?(\d+\.\d+\.\d+)$", "assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=(),
        install={},
        category="loader",
        tags=(),
        kind="modloader",
        provides={"bepinex.bepinex": "{version}"},
        supply={"bepinex:core": "{Sprocket}/BepInEx/core"},
        releases=(release,),
    )


def _loader_table(sprocket_range: str = "<0.2.54.0") -> dict:
    return {"entries": [{"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": sprocket_range}]}


def record_installed_loader(game: Path, version: str = "0.7.3") -> None:
    """把加载器写进安装记录并在磁盘上留下文件：安装状态只认这条记录。"""
    (game / "version.dll").write_bytes(b"loader")
    StateStore(state_file_path(game)).save(
        {
            "packages": {
                LOADER_ID: {
                    "name": "MelonLoader",
                    "repository": "LavaGang/MelonLoader",
                    "version": version,
                    "requested": True,
                    "files": ["version.dll"],
                }
            },
            "files": {"version.dll": {"sha256": "", "owners": [LOADER_ID]}},
        }
    )


def detected_melonloader(game: Path) -> None:
    """磁盘上的原生 MelonLoader 布局（版本从 DLL 的 PE 版本读），不进安装记录。"""
    (game / "version.dll").write_bytes(b"proxy")
    (game / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_MOD, game / "MelonLoader" / "net6" / "MelonLoader.dll")


def detected_bridge_layout(game: Path) -> None:
    """磁盘上的桥接布局：运行时安家在 `MLLoader/` 下，不进安装记录。"""
    (game / "MLLoader" / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_MOD, game / "MLLoader" / "MelonLoader" / "net6" / "MelonLoader.dll")


BRIDGE_ID = "1499501762.bepinex-melonloader-loader"


def bridge_package() -> RegistryPackage:
    """桥接加载器：记录在案时，磁盘上那份 MelonLoader 运行时就是它供给的。"""
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
        provides={MELONLOADER_CAPABILITY: "0.7.3"},
        supply={"melonloader:mod": "{Sprocket}/MLLoader/Mods"},
    )


def bridge_with_release() -> RegistryPackage:
    """桥接加载器带一条内嵌发布：解析它不需要联网。"""
    release = ReleaseInfo(
        id=2,
        tag="v0.7.3",
        version=Version(0, 7, 3),
        prerelease=False,
        published_at="",
        assets=(
            ReleaseAsset(
                id=2,
                name="MLLoader.zip",
                size=1,
                download_url=(
                    "https://github.com/1499501762/BepInEx.MelonLoader.Loader/"
                    "releases/download/v0.7.3/MLLoader.zip"
                ),
            ),
        ),
        page_url="https://github.com/1499501762/BepInEx.MelonLoader.Loader/releases/tag/v0.7.3",
    )
    return replace(
        bridge_package(),
        release={"version_pattern": r"^v?(\d+\.\d+\.\d+)$", "assets": {"include": ["*.zip"], "exclude": []}},
        releases=(release,),
    )


def prepared_bridge(root: Path, bridge: RegistryPackage) -> PreparedPlan:
    """桥接加载器的落盘计划：游戏根目录下的 `MLLoader` 树加代理 DLL。"""
    resolved = ResolvedPackage(bridge, bridge.releases[0], ())
    plan = ResolutionPlan(bridge.id, (resolved,))
    work = root / "bridge-work"
    work.mkdir(exist_ok=True)
    files = []
    for index, (relative, content) in enumerate({
        "MLLoader/MelonLoader/net6/MelonLoader.dll": b"bridge",
        "winhttp.dll": b"bridge proxy",
    }.items()):
        source = root / f"bridge-source-{index}"
        source.write_bytes(content)
        files.append(
            PreparedFile(bridge.id, source, relative, relative, sha256_file(source))
        )
    return PreparedPlan(plan, [PreparedPackage(resolved, files=files)], work)


def record_installed_bridge(game: Path, version: str = "0.7.3") -> None:
    relative = "MLLoader/MelonLoader/net6/MelonLoader.dll"
    (game / "MLLoader" / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    (game / relative).write_bytes(b"bridge")
    StateStore(state_file_path(game)).save(
        {
            "packages": {
                BRIDGE_ID: {
                    "name": "MLLoader",
                    "repository": "1499501762/BepInEx.MelonLoader.Loader",
                    "version": version,
                    "requested": True,
                    "files": [relative],
                }
            },
            "files": {relative: {"sha256": "", "owners": [BRIDGE_ID]}},
        }
    )


class GameVersionTests(unittest.TestCase):
    def test_reads_the_four_segment_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), unity_payload("0.2.53.2"))

            version = read_game_version(game)

        self.assertEqual(version.state, STATE_OK)
        self.assertEqual(version.version, "0.2.53.2")
        self.assertEqual(version.raw, "0.2.53.2")
        self.assertEqual(version.source, "Sprocket_Data/globalgamemanagers")

    def test_reads_a_future_four_segment_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), unity_payload("1.4.2.0"))

            version = read_game_version(game)

        self.assertEqual(version.state, STATE_OK)
        self.assertEqual(version.version, "1.4.2.0")

    def test_an_old_two_segment_version_is_reported_as_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), b"2022.3.62f2\x00" + b"0.127\x00")

            version = read_game_version(game)

        self.assertEqual(version.state, STATE_LEGACY)
        self.assertEqual(version.raw, "0.127")
        self.assertIn("太老", version.detail)

    def test_a_file_without_a_version_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), b"\x00\x01\x02nothing here")

            version = read_game_version(game)

        self.assertEqual(version.state, STATE_UNREADABLE)
        self.assertEqual(version.version, "")

    def test_a_missing_file_is_unreadable_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            version = read_game_version(Path(directory) / "game")

        self.assertEqual(version.state, STATE_UNREADABLE)
        self.assertIn("globalgamemanagers", version.detail)


class EnvironmentApiTests(unittest.TestCase):
    def _api(self, root: Path, game: Path | str | None) -> ClientApi:
        app_dir = root / "app"
        game_path = str(game) if game else ""
        ConfigStore(app_dir).save({"language": "zh", "game_path": game_path, "index_url": ""})
        service = ModManagerService(app_dir)
        return ClientApi("test", app_dir=app_dir, service_factory=lambda _app_dir: service)

    def _close(self, api: ClientApi) -> None:
        api._environment_monitor.stop()
        api.install_queue.close()

    def test_an_unusable_game_path_reports_an_unconfigured_environment(self) -> None:
        # 配了一个没有 Sprocket.exe 的路径：不能回落到自动探测（本机有游戏，测试要确定性地"没配好"）
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), Path(directory) / "missing-game")
            try:
                result = api.get_environment()
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["sprocket"]["state"], STATE_UNCONFIGURED)
        self.assertEqual(result["loaders"], {}, "还没加载注册表就没有加载器可报")
        self.assertEqual(result["environment"]["state"], "unknown")
        self.assertEqual(result["environment"]["table_source"], "missing", "本地不放内置表")
        self.assertIsInstance(result["revision"], int)

    def test_reads_the_game_version_and_the_loader_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), unity_payload("0.2.53.2"))
            api = self._api(Path(directory), game)
            try:
                result = api.get_environment()
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["sprocket"]["state"], STATE_OK)
        self.assertEqual(result["sprocket"]["version"], "0.2.53.2")
        self.assertEqual(result["loaders"], {})
        self.assertEqual(result["environment"]["state"], "unknown", "没有加载器版本就判不了这层")

    def test_the_running_game_process_is_reported_and_can_be_ended(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), unity_payload("0.2.53.2"))
            api = self._api(Path(directory), game)
            running = {4242: game / "Sprocket.exe"}
            try:
                with patch(
                    "sprocket_mod_manager.utilities.processes.running_executables",
                    return_value=running,
                ):
                    result = api.get_environment()
                with (
                    patch(
                        "sprocket_mod_manager.utilities.processes.running_executables",
                        return_value=running,
                    ),
                    patch(
                        "sprocket_mod_manager.utilities.processes.subprocess.run",
                        return_value=SimpleNamespace(returncode=0),
                    ) as taskkill,
                ):
                    killed = api.kill_sprocket()
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["sprocket_running"])
        self.assertTrue(killed["ok"], killed)
        self.assertEqual(killed["killed"], [4242])
        self.assertEqual(taskkill.call_args.args[0][:3], ["taskkill", "/PID", "4242"])

    def test_an_unconfigured_game_path_has_no_running_game_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), Path(directory) / "missing-game")
            try:
                result = api.get_environment()
                killed = api.kill_sprocket()
            finally:
                self._close(api)

        self.assertFalse(result["sprocket_running"])
        self.assertFalse(killed["ok"])
        self.assertEqual(killed["code"], "game_path_required")

    def test_a_installed_loader_is_reported_with_its_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), unity_payload("0.2.53.2"))
            api = self._api(Path(directory), game)
            api.service.registry = Registry([loader_package()], _loader_table())
            record_installed_loader(game)
            try:
                result = api.get_environment()
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["loaders"][LOADER_ID]["installed"], True)
        self.assertEqual(result["loaders"][LOADER_ID]["version"], "0.7.3")
        self.assertEqual(result["loaders"][LOADER_ID]["used_version"], "0.7.3")
        self.assertEqual(result["environment"]["state"], "ok", "0.7.3 支持 0.2.53.2")
        self.assertEqual(result["environment"]["table_source"], "registry")

    def test_a_detected_but_unrecorded_loader_counts_on_both_endpoints(self) -> None:
        """管理器之外装上的加载器只有一个可见处：记录里没有，磁盘上有。两处必须同答案。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            detected_melonloader(game)
            api = self._api(root, game)
            api.service.registry = Registry([loader_package()], _loader_table())
            try:
                page = api.get_modloaders()
                sidebar = api.get_environment()
            finally:
                self._close(api)

        self.assertTrue(page["ok"], page)
        item = page["modloaders"][0]
        self.assertTrue(item["installed"], "磁盘上有运行时就不能说「未安装」")
        self.assertEqual(item["installed_version"], DETECTED_LOADER_VERSION)
        self.assertFalse(item["update_available"], "最新版 0.7.3 比检测到的更旧")
        self.assertEqual(sidebar["loaders"][LOADER_ID]["installed"], True)
        self.assertEqual(sidebar["loaders"][LOADER_ID]["version"], DETECTED_LOADER_VERSION)

    def test_a_recorded_bridge_supplies_the_loader_without_double_counting(self) -> None:
        """桥接加载器记录在案时，磁盘上那份运行时就是它的：不另算一份原生 MelonLoader。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            detected_melonloader(game)
            api = self._api(root, game)
            api.service.registry = Registry([loader_package(), bridge_package()], _loader_table())
            record_installed_bridge(game)
            try:
                page = api.get_modloaders()
                sidebar = api.get_environment()
            finally:
                self._close(api)

        self.assertTrue(sidebar["loaders"][BRIDGE_ID]["installed"], "记录里的桥接加载器在场")
        self.assertFalse(sidebar["loaders"][LOADER_ID]["installed"], "运行时由桥接供给，不另算一份")
        self.assertFalse(page["modloaders"][0]["installed"])

    def test_a_detected_bridge_is_adopted_and_supplies_its_capability(self) -> None:
        """只在磁盘上的桥接布局要被认领，并把 `provides` 的能力版本给出去。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            detected_bridge_layout(game)
            api = self._api(root, game)
            api.service.registry = Registry([bridge_package()], _loader_table())
            try:
                adopted = api.adopt_existing()
                # 认领刚改过安装记录：环境读数要重来一次，与启动路径同序。
                api._environment_monitor.invalidate()
                environment = api.current_environment()
            finally:
                self._close(api)

        self.assertTrue(adopted["ok"], adopted)
        self.assertTrue(adopted["changed"], adopted)
        self.assertEqual(environment.capability_version(MELONLOADER_CAPABILITY), "0.7.3")
        self.assertEqual(
            environment.verdict([{"id": MELONLOADER_CAPABILITY, "version": ">=0.7.0"}]),
            COMPATIBLE,
        )

    def test_nothing_recorded_and_nothing_detected_is_absent_on_both_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            api = self._api(root, game)
            api.service.registry = Registry([loader_package()], _loader_table())
            try:
                page = api.get_modloaders()
                sidebar = api.get_environment()
            finally:
                self._close(api)

        item = page["modloaders"][0]
        self.assertFalse(item["installed"])
        self.assertEqual(item["installed_version"], "")
        self.assertFalse(sidebar["loaders"][LOADER_ID]["installed"])
        self.assertIsNone(sidebar["loaders"][LOADER_ID]["version"])

    def test_a_recorded_install_is_reported_by_both_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            api = self._api(root, game)
            api.service.registry = Registry([loader_package()], _loader_table())
            record_installed_loader(game)
            try:
                page = api.get_modloaders()
                sidebar = api.get_environment()
            finally:
                self._close(api)

        item = page["modloaders"][0]
        self.assertTrue(item["installed"])
        self.assertEqual(item["installed_version"], "0.7.3")
        self.assertEqual(sidebar["loaders"][LOADER_ID]["installed"], True)
        self.assertEqual(sidebar["loaders"][LOADER_ID]["version"], "0.7.3")

    def test_a_loader_that_is_only_on_disk_is_claimed_then_uninstalled(self) -> None:
        """加载器页与「已安装」页对磁盘上检测到的运行时给出的卸载都要能走通。

        那时记录里还没有它的顶层条目；交还整棵树与代理文件靠的就是那份清单，
        所以卸载前先按标识符登记这一次。
        """
        for action in ("remove_modloader", "remove"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                game = game_dir_with_version(root, unity_payload("0.2.53.2"))
                detected_melonloader(game)
                api = self._api(root, game)
                api.service.registry = Registry([loader_package()], _loader_table())
                try:
                    result = getattr(api, action)(LOADER_ID)
                    tree_gone = not (game / "MelonLoader").exists()
                    proxy_gone = not (game / "version.dll").exists()
                finally:
                    self._close(api)

                self.assertTrue(result["ok"], result)
                self.assertTrue(tree_gone, "加载器自己的树整棵交还")
                self.assertTrue(proxy_gone, "代理文件也要清掉")

    def test_removing_a_loader_refreshes_the_environment_payload(self) -> None:
        """卸载会改掉加载器清单，环境读数不能停在缓存里那份。

        轮询线程先停掉：读数只能由这次卸载自己作废，否则一秒一次的指纹兜底会掩盖漏掉的那次。
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            api = self._api(root, game)
            api.service.registry = Registry([loader_package()], _loader_table())
            record_installed_loader(game)
            try:
                before = api.get_environment()
                api._environment_monitor.stop()
                removed = api.remove(LOADER_ID)
                after = api.get_environment()
            finally:
                self._close(api)

        self.assertTrue(removed["ok"], removed)
        self.assertTrue(before["loaders"][LOADER_ID]["installed"])
        self.assertFalse(after["loaders"][LOADER_ID]["installed"], "卸载后环境读数要跟上")
        self.assertGreater(after["revision"], before["revision"])

    def test_a_queued_install_refreshes_the_environment_payload(self) -> None:
        """队列装的就是加载器时也一样：装完环境读数要重来一次。"""
        with tempfile.TemporaryDirectory() as directory:
            service = SimpleNamespace(install=lambda *_args, **_kwargs: None)
            entry = SimpleNamespace(
                package_id="test.mod",
                game_path=Path("game"),
                context=service,
                force_conflicts=False,
                version_range="*",
            )
            api = self._api(Path(directory), None)
            try:
                api._environment_monitor.stop()
                before = api.environment_snapshot()["revision"]
                api._run_queued_install(entry, lambda _message: None)
                after = api.environment_snapshot()["revision"]
            finally:
                self._close(api)

        self.assertGreater(after, before)

    def test_the_install_plan_lists_the_loader_sharing_the_capability(self) -> None:
        """两个加载器供给同一项能力：装其中一个之前，确认框先说明另一个会被交还。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            api = self._api(root, game)
            api.service.registry = Registry(
                [loader_package(), bridge_with_release()], _loader_table()
            )
            record_installed_loader(game)
            try:
                result = api.plan_install([BRIDGE_ID])
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [item["id"] for item in result["plans"][0]["displaces"]],
            [LOADER_ID],
        )

    def test_the_install_plan_lists_the_other_base_runtime(self) -> None:
        """两个基础运行时抢同一个引导槽：装 BepInEx 之前先说清官方 MelonLoader 会被交还。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            api = self._api(root, game)
            api.service.registry = Registry(
                [loader_package(), bepinex_package()], _loader_table()
            )
            record_installed_loader(game)
            try:
                result = api.plan_install([BEPINEX_ID])
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [item["id"] for item in result["plans"][0]["displaces"]],
            [LOADER_ID],
        )

    def test_installing_a_loader_uninstalls_the_loader_sharing_its_capability(self) -> None:
        """装桥接加载器会把官方加载器交还掉，并先把它那棵树归档进备份区。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            (game / "MelonLoader" / "net6").mkdir(parents=True)
            (game / "MelonLoader" / "net6" / "MelonLoader.dll").write_bytes(b"loader")
            (game / "version.dll").write_bytes(b"proxy")
            api = self._api(root, game)
            bridge = bridge_with_release()
            api.service.registry = Registry([loader_package(), bridge], _loader_table())
            api.service._installer_for(game).adopt_loader(
                loader_package(),
                game,
                version="0.7.3",
                directories=("MelonLoader",),
                payload_files=(("version.dll", sha256_file(game / "version.dll")),),
            )
            plan = ResolutionPlan(BRIDGE_ID, (ResolvedPackage(bridge, bridge.releases[0], ()),))
            try:
                with (
                    patch(
                        "sprocket_mod_manager.infrastructure.installer.sprocket_is_running",
                        return_value=False,
                    ),
                    patch.object(api.service, "resolve", return_value=plan),
                    patch.object(api.service, "prepare", return_value=prepared_bridge(root, bridge)),
                ):
                    api.service.install(BRIDGE_ID, game)
                installed = api.service.installed(game)
                tree_gone = not (game / "MelonLoader").exists()
                archives = sorted(
                    (game / "SprocketModManager" / "backup" / "replaced").rglob("*.zip")
                )
            finally:
                self._close(api)

        self.assertNotIn(LOADER_ID, installed, "供给同一能力的旧加载器要让位")
        self.assertIn(BRIDGE_ID, installed)
        self.assertTrue(tree_gone, "交还的是它自己声明的目录树")
        self.assertTrue(archives, "交还前先把它的树归档进保留的备份区")

    def test_installing_a_modloader_reports_the_files_it_wrote(self) -> None:
        """基础运行时不逐文件记账，写入数由这次安装自己报出来（这里真的走一遍落盘）。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            api = self._api(root, game)
            loader = loader_package()
            api.service.registry = Registry([loader], _loader_table())
            resolved = ResolvedPackage(loader, loader.releases[0], ())
            plan = ResolutionPlan(LOADER_ID, (resolved,))
            source_loader = root / "MelonLoader.dll"
            source_loader.write_bytes(b"loader")
            source_proxy = root / "version.dll"
            source_proxy.write_bytes(b"proxy")
            work_dir = root / "work"
            work_dir.mkdir()
            prepared = PreparedPlan(
                plan,
                [
                    PreparedPackage(
                        resolved,
                        files=[
                            PreparedFile(
                                LOADER_ID,
                                source_loader,
                                "MelonLoader/net6/MelonLoader.dll",
                                "MelonLoader/net6/MelonLoader.dll",
                                sha256_file(source_loader),
                            ),
                            PreparedFile(
                                LOADER_ID,
                                source_proxy,
                                "version.dll",
                                "version.dll",
                                sha256_file(source_proxy),
                            ),
                        ],
                    )
                ],
                work_dir,
            )
            try:
                with patch.object(api.service, "resolve", return_value=plan), patch.object(
                    api.service, "prepare", return_value=prepared
                ):
                    result = api.install_modloader(LOADER_ID)
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files_installed"], 2, "两个文件真落盘：加载器 DLL 与代理 DLL")

    def test_a_game_newer_than_the_loader_supports_is_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), unity_payload("0.2.54.2"))
            api = self._api(Path(directory), game)
            api.service.registry = Registry([loader_package()], _loader_table())
            record_installed_loader(game)
            try:
                result = api.get_environment()
            finally:
                self._close(api)

        self.assertEqual(result["environment"]["state"], "conflict")
        self.assertEqual(
            result["environment"]["entry"],
            {"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"},
        )

    def test_the_table_from_the_index_is_cached_and_reused_without_a_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.54.2"))
            api = self._api(root, game)
            try:
                # 第一次：索引里带着表 → 用注册表那份，并写进缓存。
                api.service.load_registry(_index_file(root), refresh=True)
                api._environment_monitor.note_latest_loaders({LOADER_ID: "0.7.3"})
                indexed = api.get_environment()
                self.assertEqual(indexed["environment"]["table_source"], "registry")
                self.assertEqual(indexed["environment"]["state"], "conflict")
            finally:
                self._close(api)

            # 第二次：还没拉索引（新实例）→ 用缓存那份，结论一样。
            api = self._api(root, game)
            try:
                api._environment_monitor.note_latest_loaders({LOADER_ID: "0.7.3"})
                cached = api.get_environment()
            finally:
                self._close(api)

        self.assertEqual(cached["environment"]["table_source"], "cache")
        self.assertEqual(cached["environment"]["state"], "conflict")

    def test_the_loader_is_listed_with_its_supply_and_latest_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.53.2"))
            api = self._api(root, game)
            api.service.registry = Registry([loader_package()], _loader_table())
            try:
                result = api.get_modloaders()
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        item = result["modloaders"][0]
        self.assertEqual(item["id"], LOADER_ID)
        self.assertEqual(item["page_url"], "https://github.com/LavaGang/MelonLoader")
        self.assertEqual(item["latest_version"], "0.7.3")
        self.assertFalse(item["installed"])
        self.assertEqual(item["installed_version"], "")
        self.assertFalse(item["update_available"])
        self.assertEqual(item["compatible"], "unknown", "没有环境声明就是未知")
        self.assertEqual(
            item["supply"],
            [
                {"type": "melonloader:mod", "directory": "{Sprocket}/Mods"},
                {"type": "melonloader:plugin", "directory": "{Sprocket}/Plugins"},
            ],
        )


class EnvironmentUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (CLIENT_UI / "index.html").read_text(encoding="utf-8")
        self.javascript = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted((CLIENT_UI / "js").glob("*.js"))
        )

    def test_the_versions_sit_above_the_language_control(self) -> None:
        self.assertIn('id="environment-sprocket"', self.html)
        self.assertIn('id="environment-loaders"', self.html)
        self.assertLess(
            self.html.index('id="environment"'),
            self.html.index('id="language-select"'),
            "环境信息在界面语言之上",
        )

    def test_the_bridge_is_waited_for_and_diagnostics_never_break_startup(self) -> None:
        """WebView2 会先注入空壳再挂方法：启动期别把「还没有这个方法」当成接口不存在。"""
        core = (CLIENT_UI / "js" / "core.js").read_text(encoding="utf-8")
        main = (CLIENT_UI / "js" / "main.js").read_text(encoding="utf-8")

        self.assertIn("while (!window.pywebview?.api?.[method])", core)
        self.assertIn("function traceStartup(message)", core)
        self.assertIn('startup_trace?.(String(message))', core, "诊断打点允许还不存在")
        self.assertNotIn('callApi("startup_trace"', main, "打点不再走 callApi")
        self.assertIn("traceStartup(" , main)
        self.assertIn('code: "client_startup_failed"', main, "启动失败要有自己的错误码")

    def test_the_statusbar_shows_only_health_and_toasts_the_message(self) -> None:
        """状态栏只报当前状况（派生），`setStatus` 那句话改走 toast。"""
        core = (CLIENT_UI / "js" / "core.js").read_text(encoding="utf-8")
        html = self.html

        self.assertIn('data-i18n="statusStarting"', html)
        self.assertNotIn('id="status-source"', html, "状态来源也归 toast")
        self.assertIn("function statusbarState()", core)
        self.assertIn("function renderStatusbar()", core)
        self.assertIn('tr(kind === "error" ? "statusError"', core)
        self.assertIn("toast(source ?", core, "话进 toast")
        self.assertIn("state.lastToast", core, "同一句话三秒内只弹一次")
        self.assertNotIn("state.lastError", core, "不记「上次失败」：那是另一种不实时")

    def test_the_statusbar_reports_the_running_game_and_offers_to_end_it(self) -> None:
        """右侧那行是实时读数（每秒轮询）+ 一个结束游戏的口子。"""
        core = (CLIENT_UI / "js" / "core.js").read_text(encoding="utf-8")
        modloaders = (CLIENT_UI / "js" / "modloaders.js").read_text(encoding="utf-8")
        main = (CLIENT_UI / "js" / "main.js").read_text(encoding="utf-8")

        self.assertIn('id="game-state"', self.html)
        self.assertIn('id="kill-sprocket"', self.html)
        self.assertIn("function renderGameState()", core)
        self.assertIn("state.environment.sprocket_running === true", core)
        self.assertIn('callApi("kill_sprocket")', modloaders)
        self.assertIn('$("#kill-sprocket").addEventListener("click"', main)

    def test_the_sidebar_keeps_versions_and_spells_out_the_reason(self) -> None:
        modloaders = (CLIENT_UI / "js" / "modloaders.js").read_text(encoding="utf-8")
        css = (CLIENT_UI / "app.css").read_text(encoding="utf-8")

        self.assertIn("function environmentProblem()", modloaders)
        self.assertIn("Sprocket ${sprocketText}", modloaders, "版本号照旧")
        self.assertIn("function environmentConflictText()", modloaders)
        self.assertIn('tr("environmentConflict"', modloaders)
        self.assertIn("environmentUnusable", modloaders)
        self.assertIn('id="environment-note"', self.html, "原因仍写在左下角这一行")
        self.assertIn(".environment-line.error", css)

    def test_the_install_link_starts_hidden_and_opens_the_loader_page(self) -> None:
        self.assertIn('id="environment-install-loader"', self.html)
        self.assertIn('#environment-install-loader', self.javascript)
        self.assertIn('showPage("modloaders")', self.javascript)

    def test_the_client_asks_the_backend_for_the_environment(self) -> None:
        self.assertIn('callApi("get_environment", includeLatest)', self.javascript)
        self.assertIn('callApi("get_environment", false)', self.javascript, "轮询用缓存读数")
        self.assertIn("function renderEnvironment()", self.javascript)
        self.assertIn("void refreshEnvironment(true);", self.javascript)
        self.assertIn("function pollEnvironment()", self.javascript)

    def test_the_uninstalled_loader_link_is_a_clickable_element(self) -> None:
        i18n = (CLIENT_UI / "js" / "i18n.js").read_text(encoding="utf-8")

        self.assertIn('data-i18n="installLoaderLink"', self.html)
        self.assertIn('id="environment-install-loader"', self.html)
        self.assertEqual(i18n.count("installLoaderLink:"), 2, "zh 与 en 都要有")

    def test_the_catalog_hides_incompatible_mods_behind_a_memory_only_toggle(self) -> None:
        compatibility = (CLIENT_UI / "js" / "compatibility.js").read_text(encoding="utf-8")
        catalog = (CLIENT_UI / "js" / "catalog.js").read_text(encoding="utf-8")
        core = (CLIENT_UI / "js" / "core.js").read_text(encoding="utf-8")
        settings = (CLIENT_UI / "js" / "settings.js").read_text(encoding="utf-8")

        self.assertIn('id="catalog-notice"', self.html)
        self.assertIn("function packageHidden(pkg)", compatibility)
        self.assertIn("function preferredVersion(pkg)", compatibility)
        self.assertIn("state.showIncompatible", catalog)
        self.assertIn("showIncompatible: false", core, "默认隐藏")
        self.assertNotIn("showIncompatible", settings, "开关不落盘：重启回到默认")

    def test_the_environment_poll_reloads_what_changed(self) -> None:
        modloaders = (CLIENT_UI / "js" / "modloaders.js").read_text(encoding="utf-8")
        main = (CLIENT_UI / "js" / "main.js").read_text(encoding="utf-8")

        self.assertIn("function pollEnvironment()", modloaders)
        self.assertIn("state.environmentRevision !== result.revision", modloaders)
        self.assertIn('await loadCatalog(false)', modloaders)
        self.assertIn("await refreshInstalled()", modloaders)
        self.assertIn("void pollEnvironment();", main)
        self.assertIn("}, 1000);", main, "每秒问一次环境")

    def test_an_incompatible_update_is_a_yellow_exclamation(self) -> None:
        installs = (CLIENT_UI / "js" / "installs.js").read_text(encoding="utf-8")
        i18n = (CLIENT_UI / "js" / "i18n.js").read_text(encoding="utf-8")

        self.assertIn("function incompatibleUpdateChip(version)", installs)
        self.assertIn("function incompatibleUpdateText(version)", installs)
        self.assertIn("latest?.verdict === VERDICT_INCOMPATIBLE", installs)
        self.assertIn("function installableUpdate(item)", installs)
        self.assertEqual(i18n.count("incompatibleUpdateHead:"), 2, "zh 与 en 都要有")
        self.assertEqual(i18n.count("environmentAxisAnd:"), 2)

    def test_a_compatible_verdict_is_white_not_the_container_accent(self) -> None:
        """兼容＝白：判定 chip 必须自带 `compatible` 类，否则会被所在容器（强调色）染色。"""
        compatibility = (CLIENT_UI / "js" / "compatibility.js").read_text(encoding="utf-8")
        css = (CLIENT_UI / "app.css").read_text(encoding="utf-8")

        self.assertIn('return "compatible";', compatibility)
        self.assertIn(".detail-topline > span", css, "容器确实会给 span 上色，chip 得自带类名")
        start = css.index(".state-chip.compatible {")
        rule = css[start : css.index("}", start)]
        self.assertIn("color: var(--text)", rule)

    def test_the_catalog_page_grid_has_exactly_two_rows(self) -> None:
        """目录页网格只给「页头 + 工作区」两行：说明条必须待在页头里，否则会被工作区盖住。"""
        start = self.html.index('id="page-catalog"')
        following = self.html.index('<section class="page"', start)
        section = self.html[start:following]
        children = re.findall(r'^ {12}<div class="([\w-]+)"', section, re.MULTILINE)

        self.assertEqual(children[:2], ["catalog-header", "catalog-workspace"])
        notice = section.index('id="catalog-notice"')
        tools = section.index('class="catalog-tools"')
        workspace = section.index('class="catalog-workspace"')
        self.assertTrue(notice < tools < workspace, "说明条在工具条上方，两者都在工作区之前")

        css = (CLIENT_UI / "app.css").read_text(encoding="utf-8")
        rule = css[css.index("#page-catalog.active"):]
        rule = rule[: rule.index("}")]
        self.assertIn("grid-template-rows: auto minmax(0, 1fr)", rule)
        header_rule = css[css.index(".catalog-header {"):]
        header_rule = header_rule[: header_rule.index("}")]
        self.assertIn("display: grid", header_rule)
        self.assertIn("margin-bottom: 14px", header_rule, "页头自己负责与工作区的间距")
        nested = css[css.index(".catalog-header .catalog-tools {"):]
        nested = nested[: nested.index("}")]
        self.assertIn("margin-bottom: 0", nested, "不然工具条的底边距会和页头叠成双份")

    def test_the_loader_page_shows_what_each_loader_provides_and_its_verdict(self) -> None:
        modloaders = (CLIENT_UI / "js" / "modloaders.js").read_text(encoding="utf-8")
        i18n = (CLIENT_UI / "js" / "i18n.js").read_text(encoding="utf-8")

        self.assertIn("function modloaderChip(item)", modloaders)
        self.assertIn("item.compatible === VERDICT_INCOMPATIBLE", modloaders)
        self.assertIn('modloaderSection("modloaderSupply"', modloaders, "供给表就是「提供什么、装在哪」")
        self.assertIn("entry.type", modloaders)
        self.assertIn("entry.directory", modloaders)
        self.assertIn('modloaderSection("dependencies"', modloaders)
        self.assertIn('modloaderSection("recommendations"', modloaders)
        self.assertIn("beginInstall([item.id], null, true)", modloaders, "加载器安装走模组那条安装路")
        self.assertIn('callApi("remove_modloader", item.id)', modloaders)
        for key in (
            "modloaderSupply",
            "modloaderInstalledVersion",
            "modloaderLatestVersion",
            "modloaderRemoveMessage",
        ):
            self.assertEqual(i18n.count(f"{key}:"), 2, f"{key} 要有 zh 与 en")


if __name__ == "__main__":
    unittest.main()
