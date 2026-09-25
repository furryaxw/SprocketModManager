import hashlib
import shutil
import subprocess
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.infrastructure.config import ConfigStore
from sprocket_mod_manager.infrastructure.github import RepositoryReadme
from sprocket_mod_manager.infrastructure.log_upload import LogUploadResult
from sprocket_mod_manager.infrastructure.manager_paths import state_file_path
from sprocket_mod_manager.infrastructure.state import StateStore
from sprocket_mod_manager.domain.models import RegistryPackage, ReleaseAsset, ReleaseInfo
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.domain.semver import Version
from sprocket_mod_manager.application.service import ModManagerService
from sprocket_mod_manager.presentation.controllers.settings_controller import game_directory_for
from sprocket_mod_manager.presentation.web_gui import ClientApi


def client_javascript(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((root / "js").glob("*.js"))
    )


def install_melonloader(game: Path) -> None:
    """游戏根目录的 MelonLoader 布局：扫描 `Mods` / `UserLibs` 之前得先检测到运行时。"""
    (game / "version.dll").touch()
    (game / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        Path(__file__).resolve().parent / "fixtures" / "dll_metadata" / "dll" / "FixtureMod.dll",
        game / "MelonLoader" / "net6" / "MelonLoader.dll",
    )


def bridge_package(package_id: str) -> RegistryPackage:
    return RegistryPackage(
        id=package_id,
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


class BlockingService:
    def __init__(self, _app_dir: Path):
        self.started = threading.Event()
        self.release = threading.Event()
        self.registry = None

    def install(self, _package_id, _game_path, *, version_range="*", progress=None):
        self.started.set()
        self.release.wait(2)


class FakeWindow:
    def __init__(self):
        self.destroyed = threading.Event()

    def destroy(self):
        self.destroyed.set()


class ReadmeService:
    def __init__(self, _app_dir):
        package = SimpleNamespace(id="test.mod", repository="example/TestMod")
        self.registry = SimpleNamespace(get=lambda _package_id: package)
        self.github = SimpleNamespace(
            repository_readme=lambda _repository, refresh=False: RepositoryReadme(
                html="<article><h1>Test mod</h1></article>",
                page_url="https://github.com/example/TestMod#readme",
            )
        )


def installable_package(
    package_id: str,
    file_name: str,
    *,
    recommendations: tuple[str, ...] = (),
    featured: bool = False,
    content: bytes = b"test",
) -> RegistryPackage:
    version = "1.0.0"
    asset = ReleaseAsset(
        id=1,
        name=file_name,
        size=len(content),
        download_url=f"https://github.com/test/repo/releases/download/v{version}/{file_name}",
        digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )
    release = ReleaseInfo(
        id=1,
        tag=f"v{version}",
        version=Version.parse(version),
        prerelease=False,
        published_at="",
        assets=(asset,),
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
            "overrides": [{"match": file_name, "target": "Mods"}],
        },
        category="utility",
        tags=(),
        recommendations=recommendations,
        featured=featured,
        releases=(release,),
    )


class WebGuiTests(unittest.TestCase):
    def test_open_manager_directory_uses_app_data_directory(self):
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            api = ClientApi("test", app_dir=app_dir)
            try:
                with patch("sprocket_mod_manager.presentation.web_gui.open_directory") as opener:
                    result = api.open_manager_directory()
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], str(app_dir))
        opener.assert_called_once_with(app_dir)

    def test_upload_log_routes_the_manager_source_to_the_manager_log(self):
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            api = ClientApi("test", app_dir=app_dir)
            uploaded = LogUploadResult("request", 201, 12, "https://logs.example/result")
            try:
                with patch(
                    "sprocket_mod_manager.presentation.web_gui.upload_log_file",
                    return_value=uploaded,
                ) as upload:
                    result = api.upload_log("manager")
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], uploaded.url)
        self.assertEqual(upload.call_args.args[0], app_dir / "Latest.log")

    def test_force_conflict_queue_item_reaches_service_install(self):
        with TemporaryDirectory() as directory:
            calls = []
            service = SimpleNamespace(
                install=lambda package_id, game_path, **kwargs: calls.append(
                    (package_id, game_path, kwargs)
                )
            )
            entry = SimpleNamespace(
                package_id="test.mod",
                game_path=Path("game"),
                context=service,
                force_conflicts=True,
                version_range="*",
            )
            api = ClientApi("test", app_dir=Path(directory))
            try:
                api._run_queued_install(entry, lambda _message: None)
            finally:
                api.install_queue.close()

            self.assertEqual(calls[0][0:2], ("test.mod", Path("game")))
            self.assertTrue(calls[0][2]["force_conflicts"])

    def test_client_ui_uses_packaged_application_icon(self):
        ui_root = Path(__file__).resolve().parents[1] / "sprocket_mod_manager" / "presentation" / "client_ui"
        html = (ui_root / "index.html").read_text(encoding="utf-8")

        self.assertTrue((ui_root / "app-icon.png").is_file())
        self.assertIn('rel="icon" type="image/png" href="./app-icon.png"', html)

    def test_catalog_exposes_new_install_recommendation_marker(self):
        with TemporaryDirectory() as directory:
            package = installable_package("test.featured", "Featured.dll", featured=True)
            service = SimpleNamespace(
                registry=Registry([package]),
                github=SimpleNamespace(install_assets=lambda _package, release: release.assets),
                installed=lambda _game_path, *, suppressed=(): {},
            )
            api = ClientApi("0.3.2", app_dir=Path(directory))
            try:
                packages = api._catalog_data(service, {package.id: package.releases[0]})
            finally:
                api.install_queue.close()

        self.assertTrue(packages[0]["featured"])

    def test_any_dll_under_mods_disables_new_install_recommendations(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            game = root / "game"
            mods = game / "Mods"
            mods.mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            install_melonloader(game)
            ConfigStore(app_dir).save(
                {"language": "en", "game_path": str(game), "index_url": ""}
            )
            api = ClientApi("0.3.2", app_dir=app_dir)
            try:
                self.assertFalse(api._has_any_mods())
                (mods / "UnknownMod.DLL").write_bytes(b"unmanaged")
                self.assertTrue(api._has_any_mods())
            finally:
                api.install_queue.close()

    def test_a_detected_runtime_feeds_the_capability_map(self) -> None:
        """磁盘上检测到的版本就是能力版本，不是「可安装的最新版」。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            game = root / "game"
            (game / "Mods").mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            install_melonloader(game)
            ConfigStore(app_dir).save({"language": "en", "game_path": str(game), "index_url": ""})
            loader = replace(
                installable_package("lavagang.melonloader", "MelonLoader.dll"),
                kind="modloader",
                supply={
                    "melonloader:core": "{Sprocket}/MelonLoader",
                    "melonloader:mod": "{Sprocket}/Mods",
                    "melonloader:plugin": "{Sprocket}/Plugins",
                    "melonloader:userlib": "{Sprocket}/UserLibs",
                },
            )

            api = ClientApi("0.3.2", app_dir=app_dir)
            try:
                api.service.registry = Registry([loader])
                api._environment_monitor.note_latest_loaders({"lavagang.melonloader": "9.9.9"})
                environment = api.current_environment()
            finally:
                api._environment_monitor.stop()
                api.install_queue.close()

        self.assertEqual(environment.capability_version("lavagang.melonloader"), "1.2.3")

    def test_the_environment_monitor_follows_the_bridge_directories(self) -> None:
        """指纹要覆盖活跃标识符的目录，桥接加载器的 `MLLoader/Mods` 也在其中。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            game = root / "game"
            (game / "MLLoader" / "Mods").mkdir(parents=True)
            (game / "MLLoader" / "MelonLoader").mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            (game / "MLLoader" / "MelonLoader" / "MelonLoader.dll").write_bytes(b"bridge")
            ConfigStore(app_dir).save({"language": "en", "game_path": str(game), "index_url": ""})
            bridge_id = "1499501762.bepinex-melonloader-loader"
            StateStore(state_file_path(game)).save(
                {
                    "schema_version": 2,
                    "packages": {
                        bridge_id: {"name": "MLLoader", "files": ["MLLoader/MelonLoader/MelonLoader.dll"]}
                    },
                    "files": {},
                    "metadata": {},
                }
            )

            api = ClientApi("0.3.2", app_dir=app_dir)
            try:
                api.service.registry = Registry([bridge_package(bridge_id)])
                api.environment_snapshot()
                directories = api._mod_directories
            finally:
                api._environment_monitor.stop()
                api.install_queue.close()

        self.assertIn("MLLoader/Mods", directories)

    def test_bootstrap_uses_saved_language_without_detecting_game_path(self):
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            ConfigStore(app_dir).save(
                {"language": "en", "game_path": "", "index_url": ""}
            )
            api = ClientApi("0.2.0", app_dir=app_dir)
            try:
                result = api.bootstrap()
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["settings"]["game_path"], "")
        self.assertEqual(result["settings"]["text_scale"], 100)
        self.assertEqual(
            result["settings"]["index_placeholder"],
            "https://sprocketmods.furryaxw.top/index.json",
        )

    def test_close_waits_for_running_install_then_destroys_window(self):
        with TemporaryDirectory() as directory:
            service = BlockingService(Path(directory))
            api = ClientApi(
                "0.2.0",
                app_dir=Path(directory),
                service_factory=lambda _app_dir: service,
            )
            window = FakeWindow()
            api.bind_window(window)
            api.install_queue.enqueue(["test.mod"], Path(directory))
            self.assertTrue(service.started.wait(1))

            self.assertFalse(api.on_closing())
            with self.assertRaisesRegex(RuntimeError, "queue is closed"):
                api.install_queue.enqueue(["test.other"], Path(directory))
            service.release.set()
            self.assertTrue(window.destroyed.wait(2))
            api.on_closed()

    def test_text_scale_is_saved_and_returned_to_the_client(self):
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            api = ClientApi("0.3.2", app_dir=app_dir)
            try:
                saved = api.save_settings(
                    {
                        "language": "en",
                        "game_path": "",
                        "index_url": "",
                        "proxy_enabled": True,
                        "proxy_url": "http://127.0.0.1:7890",
                        "github_proxy_enabled": True,
                        "github_proxy_url": "https://mirror.example.com",
                        "text_scale": 150,
                    }
                )
                loaded = api.get_settings()
            finally:
                api.install_queue.close()

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["settings"]["text_scale"], 150)
        self.assertEqual(loaded["settings"]["text_scale"], 150)
        self.assertTrue(saved["settings"]["proxy_enabled"])
        self.assertEqual(saved["settings"]["proxy_url"], "http://127.0.0.1:7890")
        self.assertTrue(loaded["settings"]["github_proxy_enabled"])
        self.assertEqual(
            loaded["settings"]["github_proxy_url"],
            "https://mirror.example.com/",
        )

    def test_debug_config_is_persisted_and_orred_with_flag_override(self):
        with TemporaryDirectory() as directory:
            app_dir = Path(directory)
            api = ClientApi("0.3.2", app_dir=app_dir, debug_override=True)
            try:
                with patch(
                    "sprocket_mod_manager.presentation.controllers.settings_controller.set_logging_level"
                ) as set_level:
                    saved = api.save_settings({"debug": False, "language": "en"})
                    enabled = api.save_settings({"debug": True, "language": "en"})
            finally:
                api.install_queue.close()

        self.assertFalse(saved["settings"]["debug"])
        self.assertTrue(saved["settings"]["debug_active"])
        self.assertTrue(enabled["settings"]["debug"])
        self.assertEqual([call.args[0] for call in set_level.call_args_list], [True, True])

    def test_enabled_empty_proxy_settings_apply_default_addresses(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("0.3.3", app_dir=Path(directory))
            try:
                saved = api.save_settings(
                    {
                        "language": "en",
                        "game_path": "",
                        "index_url": "",
                        "proxy_enabled": True,
                        "proxy_url": "",
                        "github_proxy_enabled": True,
                        "github_proxy_url": "",
                        "text_scale": 100,
                    }
                )
                proxy_url = api.service.http.proxy_url
                github_proxy_url = api.service.http.github_proxy_url
            finally:
                api.install_queue.close()

        self.assertTrue(saved["ok"])
        self.assertEqual(proxy_url, "http://127.0.0.1:7890")
        self.assertEqual(github_proxy_url, "https://gh-proxy.com/")

    def test_install_plan_returns_optional_recommendations(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").touch()
            ConfigStore(app_dir).save(
                {"language": "en", "game_path": str(game), "index_url": ""}
            )
            service = ModManagerService(app_dir)
            recommended = installable_package("test.recommended", "Recommended.dll")
            package = installable_package(
                "test.mod",
                "TestMod.dll",
                recommendations=(recommended.id,),
            )
            service.registry = Registry([package, recommended])
            api = ClientApi(
                "0.3.2",
                app_dir=app_dir,
                service_factory=lambda _app_dir: service,
            )
            try:
                result = api.plan_install([package.id])
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"])
        self.assertEqual([item["id"] for item in result["plans"]], [package.id])
        self.assertEqual(
            [item["id"] for item in result["recommendations"]],
            [recommended.id],
        )
        self.assertEqual(result["recommendations"][0]["recommended_by"], [package.id])

    def test_get_installed_automatically_adopts_exact_mods_release(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            game = root / "game"
            (game / "Mods").mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            install_melonloader(game)
            content = b"published mod"
            (game / "Mods" / "TestMod.dll").write_bytes(content)
            unknown = game / "Mods" / "UnknownMod.dll"
            unknown.write_bytes(b"unknown mod")
            ConfigStore(app_dir).save(
                {"language": "en", "game_path": str(game), "index_url": ""}
            )
            service = ModManagerService(app_dir)
            package = installable_package("test.mod", "TestMod.dll", content=content)
            service.registry = Registry([package])
            api = ClientApi(
                "0.3.2",
                app_dir=app_dir,
                service_factory=lambda _app_dir: service,
            )
            try:
                # 列表本身不认领（不访问网络）……
                listed = api.get_installed()
                # ……认领是渲染完成之后的独立调用。
                adopted_result = api.adopt_existing()
                after = api.get_installed()
            finally:
                api.install_queue.close()
            unknown_content = unknown.read_bytes()

        self.assertTrue(listed["ok"])
        self.assertTrue(adopted_result["ok"])
        self.assertTrue(adopted_result["changed"], "认领成功要报告 changed")
        self.assertEqual(after["installed"][0]["id"], package.id)
        self.assertEqual(
            after["unrecognized"],
            [{"name": "UnknownMod.dll", "path": "Mods/UnknownMod.dll"}],
        )
        self.assertEqual(unknown_content, b"unknown mod")

    def test_get_installed_reports_unrecognized_userlib_without_mods(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app_dir = root / "app"
            game = root / "game"
            userlib = game / "UserLibs" / "LocalLibrary.dll"
            userlib.parent.mkdir(parents=True)
            userlib.write_bytes(b"unmanaged library")
            (game / "Sprocket.exe").touch()
            install_melonloader(game)
            ConfigStore(app_dir).save(
                {"language": "en", "game_path": str(game), "index_url": ""}
            )
            service = ModManagerService(app_dir)
            service.registry = Registry([])
            api = ClientApi(
                "0.3.3",
                app_dir=app_dir,
                service_factory=lambda _app_dir: service,
            )
            try:
                result = api.get_installed()
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["unrecognized"],
            [{"name": "LocalLibrary.dll", "path": "UserLibs/LocalLibrary.dll"}],
        )
        self.assertFalse(result["has_any_mods"])

    def test_a_registered_package_repository_is_an_allowed_link(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("0.2.0", app_dir=Path(directory))
            loader = replace(
                installable_package("lavagang.melonloader", "MelonLoader.dll"),
                repository="LavaGang/MelonLoader",
            )
            api.service.registry = Registry([loader])
            try:
                with patch("sprocket_mod_manager.presentation.web_gui.webbrowser.open") as open_browser:
                    result = api.open_url("https://github.com/LavaGang/MelonLoader/releases")
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"])
        open_browser.assert_called_once()

    def test_package_readme_is_loaded_from_registered_repository(self):
        with TemporaryDirectory() as directory:
            api = ClientApi(
                "0.2.0",
                app_dir=Path(directory),
                service_factory=ReadmeService,
            )
            try:
                result = api.get_package_readme("test.mod", refresh=True)
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"])
        self.assertEqual(result["package_id"], "test.mod")
        self.assertIn("<h1>Test mod</h1>", result["html"])
        self.assertEqual(result["page_url"], "https://github.com/example/TestMod#readme")

    def test_the_catalog_page_reads_the_merged_reading_from_one_place(self):
        """公开注册表与私有服务器在数据层视图里合并一次，页面不再各自拼 `state.packages`。"""
        root = Path(__file__).parents[1]
        client_ui = root / "sprocket_mod_manager" / "presentation" / "client_ui"
        data = (client_ui / "js" / "data.js").read_text(encoding="utf-8")
        catalog = (client_ui / "js" / "catalog.js").read_text(encoding="utf-8")
        servers = (client_ui / "js" / "private_servers.js").read_text(encoding="utf-8")
        business = (client_ui / "js" / "business.js").read_text(encoding="utf-8")

        self.assertIn('publicPackages: () => dataValue("catalog")?.packages || []', data)
        self.assertIn('privatePackages: () => dataValue("servers")?.packages || []', data)
        self.assertIn(
            '...(dataValue("servers")?.packages || []),',
            data,
            "合并只在这一处发生",
        )
        self.assertNotIn("state.packages = ", catalog)
        self.assertNotIn("state.packages = ", servers)
        self.assertIn(
            'callApi("load_catalog", refresh)',
            catalog,
            "目录读数由数据层刷（这条命令只回执）",
        )
        self.assertIn("function watchCatalogData()", business, "页面按推送重画")

    def test_default_entry_and_assets_do_not_depend_on_tk(self):
        root = Path(__file__).parents[1]
        entry = (root / "modman.py").read_text(encoding="utf-8")
        web_gui = (root / "sprocket_mod_manager" / "presentation" / "web_gui.py").read_text(
            encoding="utf-8"
        )
        html = (
            root / "sprocket_mod_manager" / "presentation" / "client_ui" / "index.html"
        ).read_text(encoding="utf-8")
        javascript = client_javascript(
            root / "sprocket_mod_manager" / "presentation" / "client_ui"
        )

        self.assertIn("from sprocket_mod_manager.presentation.webview_app import run_gui", entry)
        self.assertNotIn("tkinter", web_gui)
        self.assertNotIn("customtkinter", web_gui)
        self.assertEqual(html.count('id="language-select"'), 1)
        self.assertIn('id="modal-layer" hidden', html)
        self.assertIn('id="modloader-list"', html)
        self.assertIn('id="text-scale"', html)
        self.assertNotIn("window.alert", javascript)
        self.assertNotIn("window.confirm", javascript)
        self.assertIn("async function installLoader", javascript)
        self.assertIn("function sanitizeReadmeHtml", javascript)
        self.assertIn('callApi("get_package_readme"', javascript)
        self.assertIn("metadata.textContent = localized(pkg.description, pkg.id)", javascript)
        self.assertIn('heading.className = "detail-heading"', javascript)
        self.assertIn('readmeDetails.className = "detail-readme"', javascript)
        self.assertIn(
            "appendCompatibility(block, pkg, verdict)",
            javascript,
            "兼容性细节是详情块的最后一段",
        )
        self.assertIn('"enqueue_install",', javascript)
        self.assertIn("function applyTextScale", javascript)
        self.assertIn("checkbox.checked = planState.recommendedSelection.has(plan.id)", javascript)
        self.assertIn('className: "installed"', javascript)
        self.assertIn(".state-chip.installed", (
            root / "sprocket_mod_manager" / "presentation" / "client_ui" / "app.css"
        ).read_text(encoding="utf-8"))
        self.assertIn("result.recommendations || []", javascript)
        self.assertIn("if (featured) return featured", javascript)
        self.assertIn('star.textContent = "★"', javascript)
        self.assertIn('tr("starterRecommended")', javascript)
        self.assertIn("function showStarterRecommendations()", javascript)
        self.assertIn('unrecognized: () => dataValue("installed")?.unrecognized || []', javascript)
        self.assertIn('status.textContent = tr("unrecognized")', javascript)
        self.assertIn("if (item.unrecognized)", javascript)
        self.assertIn('class="brand-line" aria-hidden="true"', html)
        self.assertIn('id="open-manager-directory"', html)
        self.assertIn('id="upload-logs"', html)
        self.assertIn('id="debug-mode"', html)
        self.assertIn('callApi("open_manager_directory")', javascript)
        self.assertIn('"upload_log"', javascript)

    def test_catalog_columns_share_one_bounded_scroll_area(self):
        css = (
            Path(__file__).parents[1]
            / "sprocket_mod_manager"
            / "presentation"
            / "client_ui"
            / "app.css"
        ).read_text(encoding="utf-8")

        self.assertIn("#page-catalog.active", css)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr)", css)
        self.assertRegex(
            css,
            r"\.package-list, \.detail-panel \{[^}]*height: 100%;[^}]*overflow: auto;",
        )
        self.assertNotIn("max-height: calc(100vh", css)
        self.assertRegex(
            css,
            r"\.package-copy span \{[^}]*font: 0\.75rem/1\.0625rem[^}]*white-space: normal;",
        )
        self.assertNotRegex(css, r"font-size:\s*[0-9]+px")
        self.assertNotRegex(css, r"font:\s*[^;/]*\s[0-9]+px/")

    def test_translations_use_a_separate_client_page(self):
        root = Path(__file__).parents[1] / "sprocket_mod_manager" / "presentation" / "client_ui"
        html = (root / "index.html").read_text(encoding="utf-8")
        javascript = client_javascript(root)

        self.assertIn('data-page-target="translations"', html)
        self.assertIn('id="page-translations" data-page="translations"', html)
        self.assertIn('id="translation-list"', html)
        self.assertIn('pkg.category === "translation") !== translations', javascript)
        self.assertRegex(
            javascript,
            r'translations:\s*\{\s*kicker:\s*"pageLocalization"',
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the client initialization smoke test")
    def test_translation_dictionary_initializes_without_tdz_errors(self):
        script = (
            Path(__file__).parents[1]
            / "sprocket_mod_manager"
            / "presentation"
            / "client_ui"
            / "js"
            / "i18n.js"
        )
        result = subprocess.run(
            [shutil.which("node"), str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_select_options_use_the_dark_palette(self):
        css = (
            Path(__file__).parents[1]
            / "sprocket_mod_manager"
            / "presentation"
            / "client_ui"
            / "app.css"
        ).read_text(encoding="utf-8")

        self.assertRegex(
            css,
            r"select option \{[^}]*color: var\(--text\);[^}]*background: #111516;",
        )

    def test_client_palette_matches_the_registry_site(self):
        root = Path(__file__).parents[1]
        site_css = (root / "site" / "styles.css").read_text(encoding="utf-8")
        client_css = (
            root / "sprocket_mod_manager" / "presentation" / "client_ui" / "app.css"
        ).read_text(encoding="utf-8")
        shared_variables = (
            "canvas",
            "line",
            "line-soft",
            "text",
            "text-soft",
            "muted",
            "accent",
            "accent-hover",
            "accent-quiet",
            "button-accent",
            "button-accent-hover",
            "focus",
        )
        for name in shared_variables:
            marker = f"--{name}: "
            site_value = site_css.split(marker, 1)[1].split(";", 1)[0]
            client_value = client_css.split(marker, 1)[1].split(";", 1)[0]
            self.assertEqual(client_value, site_value, name)


class ChooseGamePathTests(unittest.TestCase):
    """游戏位置按钮选的是 `Sprocket.exe`，写进配置的是它所在的目录。"""

    class _Window:
        def __init__(self, selection: str) -> None:
            self.selection = selection
            self.dialogs: list[tuple[object, dict]] = []

        def create_file_dialog(self, dialog_type: object, **kwargs: object) -> tuple[str, ...] | None:
            self.dialogs.append((dialog_type, kwargs))
            return (self.selection,) if self.selection else None

    def test_a_selected_executable_maps_to_its_directory(self) -> None:
        self.assertEqual(
            game_directory_for(str(Path("C:/Games/Sprocket") / "Sprocket.exe")),
            str(Path("C:/Games/Sprocket")),
        )

    def test_cancelling_the_dialog_keeps_the_path_empty(self) -> None:
        self.assertEqual(game_directory_for(""), "")

    def test_a_selected_directory_stays_as_it_is(self) -> None:
        with TemporaryDirectory() as directory:
            self.assertEqual(game_directory_for(directory), str(Path(directory)))

    def test_the_dialog_is_a_file_picker_for_the_executable(self) -> None:
        import webview

        with TemporaryDirectory() as directory:
            game = Path(directory) / "Sprocket"
            game.mkdir()
            window = self._Window(str(game / "Sprocket.exe"))
            api = ClientApi("test", app_dir=Path(directory) / "app")
            api.bind_window(window)
            try:
                result = api.choose_game_path()
            finally:
                api.install_queue.close()

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["path"], str(game))
        dialog_type, kwargs = window.dialogs[0]
        self.assertEqual(dialog_type, webview.FileDialog.OPEN, "选的是文件，不是文件夹")
        self.assertNotEqual(dialog_type, webview.FileDialog.FOLDER)
        self.assertEqual(kwargs.get("file_types"), ("Sprocket (*.exe)",))


if __name__ == "__main__":
    unittest.main()
