"""本机环境（Sprocket 版本 + MelonLoader 版本）的读取与展示契约。"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.application.service import ModManagerService
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
from sprocket_mod_manager.domain.models import ReleaseAsset
from sprocket_mod_manager.infrastructure.melonloader import (
    MelonLoaderInstallResult,
    MelonLoaderInstallation,
    MelonLoaderRelease,
)
from sprocket_mod_manager.presentation.web_gui import ClientApi

ROOT = Path(__file__).resolve().parents[1]
CLIENT_UI = ROOT / "sprocket_mod_manager" / "presentation" / "client_ui"


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
    """一个能过 Registry 校验的最小索引，顶层带着环境表（注册表那份的来源）。"""
    path = root / "index.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "game": "sprocket",
                "environment": {
                    "schema_version": 1,
                    "entries": [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": sprocket_range}],
                },
                "packages": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _install_result() -> MelonLoaderInstallResult:
    release = MelonLoaderRelease(
        tag="v0.7.3",
        version=Version(0, 7, 3),
        page_url="https://github.com/LavaGang/MelonLoader/releases/tag/v0.7.3",
        asset=ReleaseAsset(
            id=1,
            name="MelonLoader.x64.zip",
            size=1,
            download_url=(
                "https://github.com/LavaGang/MelonLoader/releases/download/v0.7.3/MelonLoader.x64.zip"
            ),
        ),
    )
    return MelonLoaderInstallResult(
        release=release, files_installed=3, sha256="0" * 64, publisher_verified=True
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
        self.assertFalse(result["melonloader"]["installed"])
        self.assertIsNone(result["melonloader"]["version"])
        self.assertIsNone(result["melonloader"]["used_version"])
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
        self.assertFalse(result["melonloader"]["installed"])
        self.assertIsNone(result["melonloader"]["version"])
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
            installation = MelonLoaderInstallation(installed=True, version=Version(0, 7, 3))
            api.service.registry = Registry(
                [], {"entries": [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}]}
            )
            try:
                with patch.object(api.melonloader, "detect", return_value=installation):
                    result = api.get_environment()
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["melonloader"]["installed"], True)
        self.assertEqual(result["melonloader"]["version"], "0.7.3")
        self.assertEqual(result["melonloader"]["used_version"], "0.7.3")
        self.assertEqual(result["environment"]["state"], "ok", "0.7.3 支持 0.2.53.2")
        self.assertEqual(result["environment"]["table_source"], "registry")

    def test_a_game_newer_than_the_loader_supports_is_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = game_dir_with_version(Path(directory), unity_payload("0.2.54.2"))
            api = self._api(Path(directory), game)
            installation = MelonLoaderInstallation(installed=True, version=Version(0, 7, 3))
            api.service.registry = Registry(
                [], {"entries": [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}]}
            )
            try:
                with patch.object(api.melonloader, "detect", return_value=installation):
                    result = api.get_environment()
            finally:
                self._close(api)

        self.assertEqual(result["environment"]["state"], "conflict")
        self.assertEqual(
            result["environment"]["entry"],
            {"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"},
        )

    def test_the_table_from_the_index_is_cached_and_reused_without_a_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.54.2"))
            api = self._api(root, game)
            installation = MelonLoaderInstallation(installed=True, version=Version(0, 7, 3))
            try:
                # 第一次：索引里带着表 → 用注册表那份，并写进缓存。
                api.service.load_registry(_index_file(root), refresh=True)
                with patch.object(api.melonloader, "detect", return_value=installation):
                    indexed = api.get_environment()
                self.assertEqual(indexed["environment"]["table_source"], "registry")
                self.assertEqual(indexed["environment"]["state"], "conflict")
            finally:
                self._close(api)

            # 第二次：还没拉索引（新实例）→ 用缓存那份，结论一样。
            api = self._api(root, game)
            try:
                with patch.object(api.melonloader, "detect", return_value=installation):
                    cached = api.get_environment()
            finally:
                self._close(api)

        self.assertEqual(cached["environment"]["table_source"], "cache")
        self.assertEqual(cached["environment"]["state"], "conflict")

    def test_installing_melonloader_reports_whether_it_fits_the_game(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = game_dir_with_version(root, unity_payload("0.2.54.2"))
            api = self._api(root, game)
            api.service.registry = Registry(
                [], {"entries": [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}]}
            )
            installation = MelonLoaderInstallation(installed=True, version=Version(0, 7, 3))
            with patch.object(
                api.melonloader, "install", return_value=_install_result()
            ), patch.object(api.melonloader, "detect", return_value=installation):
                response = api.install_melonloader()
            self._close(api)

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["compatibility"]["state"], "conflict")
        self.assertEqual(response["compatibility"]["sprocket"], "0.2.54.2")


class EnvironmentUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (CLIENT_UI / "index.html").read_text(encoding="utf-8")
        self.javascript = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted((CLIENT_UI / "js").glob("*.js"))
        )

    def test_the_versions_sit_above_the_language_control(self) -> None:
        self.assertIn('id="environment-sprocket"', self.html)
        self.assertIn('id="environment-melonloader-text"', self.html)
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
        melonloader = (CLIENT_UI / "js" / "melonloader.js").read_text(encoding="utf-8")
        main = (CLIENT_UI / "js" / "main.js").read_text(encoding="utf-8")

        self.assertIn('id="game-state"', self.html)
        self.assertIn('id="kill-sprocket"', self.html)
        self.assertIn("function renderGameState()", core)
        self.assertIn("state.environment.sprocket_running === true", core)
        self.assertIn('callApi("kill_sprocket")', melonloader)
        self.assertIn('$("#kill-sprocket").addEventListener("click"', main)

    def test_the_sidebar_keeps_versions_and_spells_out_the_reason(self) -> None:
        melonloader = (CLIENT_UI / "js" / "melonloader.js").read_text(encoding="utf-8")
        css = (CLIENT_UI / "app.css").read_text(encoding="utf-8")

        self.assertIn("function environmentProblem()", melonloader)
        self.assertIn("Sprocket ${sprocketText}", melonloader, "版本号照旧")
        self.assertIn('environment.environment?.state === "conflict"', melonloader)
        self.assertIn('tr("environmentConflict"', melonloader)
        self.assertIn("environmentUnusable", melonloader)
        self.assertIn('id="environment-note"', self.html, "原因仍写在左下角这一行")
        self.assertIn(".environment-line.error", css)

    def test_the_install_button_starts_hidden_and_shares_the_settings_action(self) -> None:
        self.assertIn('id="environment-install-melonloader"', self.html)
        self.assertIn('#environment-install-melonloader', self.javascript)
        self.assertIn("function handleMelonLoaderAction()", self.javascript)
        self.assertEqual(
            self.javascript.count('addEventListener("click", handleMelonLoaderAction)'),
            2,
            "左下角和设置页共用同一个动作",
        )

    def test_the_client_asks_the_backend_for_the_environment(self) -> None:
        self.assertIn('callApi("get_environment", includeLatest)', self.javascript)
        self.assertIn('callApi("get_environment", false)', self.javascript, "轮询用缓存读数")
        self.assertIn("function renderEnvironment()", self.javascript)
        self.assertIn("void refreshEnvironment(true);", self.javascript)
        self.assertIn("function pollEnvironment()", self.javascript)

    def test_the_uninstalled_loader_link_is_a_clickable_element(self) -> None:
        i18n = (CLIENT_UI / "js" / "i18n.js").read_text(encoding="utf-8")

        self.assertIn('data-i18n="installMelonLoaderLink"', self.html)
        self.assertIn("点击安装 MelonLoader", i18n)
        self.assertIn("installMelonLoaderLink:", i18n)
        self.assertIn('id="environment-install-melonloader"', self.html)

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
        melonloader = (CLIENT_UI / "js" / "melonloader.js").read_text(encoding="utf-8")
        main = (CLIENT_UI / "js" / "main.js").read_text(encoding="utf-8")

        self.assertIn("function pollEnvironment()", melonloader)
        self.assertIn("state.environmentRevision !== result.revision", melonloader)
        self.assertIn('await loadCatalog(false)', melonloader)
        self.assertIn("await refreshInstalled()", melonloader)
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

    def test_installing_the_loader_consults_the_compatibility_table(self) -> None:
        melonloader = (CLIENT_UI / "js" / "melonloader.js").read_text(encoding="utf-8")
        catalog = (CLIENT_UI / "js" / "catalog.js").read_text(encoding="utf-8")
        i18n = (CLIENT_UI / "js" / "i18n.js").read_text(encoding="utf-8")

        self.assertIn("async function confirmIncompatibleLoader()", melonloader)
        self.assertIn('environment?.environment?.state !== "conflict"', melonloader)
        self.assertIn("async function installMelonLoader(skipCompatibilityCheck = false)", melonloader)
        self.assertIn('result.compatibility?.state === "conflict"', melonloader)
        self.assertIn("installMelonLoader(true)", catalog, "装模组前那条流程自己有弹窗，不再问第二遍")
        for key in (
            "melonloaderIncompatibleTitle",
            "melonloaderIncompatibleMessage",
            "melonloaderStillIncompatible",
            "melonloaderRequiredIncompatible",
            "installAnyway",
        ):
            self.assertEqual(i18n.count(f"{key}:"), 2, f"{key} 要有 zh 与 en")


if __name__ == "__main__":
    unittest.main()
