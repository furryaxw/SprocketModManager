import hashlib
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.config import ConfigStore
from sprocket_mod_manager.github import RepositoryReadme
from sprocket_mod_manager.melonloader import MelonLoaderInstallation
from sprocket_mod_manager.models import RegistryPackage, ReleaseAsset, ReleaseInfo
from sprocket_mod_manager.registry import Registry
from sprocket_mod_manager.semver import Version
from sprocket_mod_manager.service import ModManagerService
from sprocket_mod_manager.web_gui import ClientApi


class BlockingService:
    def __init__(self, _app_dir: Path):
        self.started = threading.Event()
        self.release = threading.Event()
        self.registry = None

    def install(self, _package_id, _game_path, *, progress=None):
        self.started.set()
        self.release.wait(2)


class FakeWindow:
    def __init__(self):
        self.destroyed = threading.Event()

    def destroy(self):
        self.destroyed.set()


class MissingMelonLoader:
    @staticmethod
    def detect(_game_path):
        return MelonLoaderInstallation(False, None)


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
    def test_client_ui_uses_packaged_application_icon(self):
        ui_root = Path(__file__).resolve().parents[1] / "sprocket_mod_manager" / "client_ui"
        html = (ui_root / "index.html").read_text(encoding="utf-8")

        self.assertTrue((ui_root / "app-icon.png").is_file())
        self.assertIn('rel="icon" type="image/png" href="./app-icon.png"', html)

    def test_catalog_exposes_new_install_recommendation_marker(self):
        with TemporaryDirectory() as directory:
            package = installable_package("test.featured", "Featured.dll", featured=True)
            service = SimpleNamespace(
                registry=Registry([package]),
                github=SimpleNamespace(install_assets=lambda _package, release: release.assets),
                installed=lambda _game_path: {},
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
            nested = game / "Mods" / "Nested"
            nested.mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            ConfigStore(app_dir).save(
                {"language": "en", "game_path": str(game), "index_url": ""}
            )
            api = ClientApi("0.3.2", app_dir=app_dir)
            try:
                self.assertFalse(api._has_any_mods())
                (nested / "UnknownMod.DLL").write_bytes(b"unmanaged")
                self.assertTrue(api._has_any_mods())
            finally:
                api.install_queue.close()

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

    def test_mod_enqueue_requires_explicit_confirmation_without_melonloader(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            game.mkdir()
            (game / "Sprocket.exe").touch()
            ConfigStore(root / "app").save(
                {"language": "en", "game_path": str(game), "index_url": ""}
            )
            api = ClientApi("0.2.0", app_dir=root / "app")
            api.melonloader = MissingMelonLoader()
            try:
                blocked = api.enqueue_install([])
                allowed = api.enqueue_install([], allow_without_melonloader=True)
            finally:
                api.install_queue.close()

        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["code"], "melonloader_required")
        self.assertTrue(allowed["ok"])

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
            api.melonloader = MissingMelonLoader()
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
            content = b"published mod"
            (game / "Mods" / "TestMod.dll").write_bytes(content)
            unknown = game / "Mods" / "Nested" / "UnknownMod.dll"
            unknown.parent.mkdir()
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
                result = api.get_installed()
            finally:
                api.install_queue.close()
            unknown_content = unknown.read_bytes()

        self.assertTrue(result["ok"])
        self.assertEqual([item["id"] for item in result["adopted"]], [package.id])
        self.assertTrue(result["installed"][0]["adopted"])
        self.assertEqual(
            result["unrecognized"],
            [{"name": "UnknownMod.dll", "path": "Mods/Nested/UnknownMod.dll"}],
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

    def test_official_melonloader_repository_is_an_allowed_link(self):
        with TemporaryDirectory() as directory:
            api = ClientApi("0.2.0", app_dir=Path(directory))
            try:
                with patch("sprocket_mod_manager.web_gui.webbrowser.open") as open_browser:
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

    def test_default_entry_and_assets_do_not_depend_on_tk(self):
        root = Path(__file__).parents[1]
        entry = (root / "modman.py").read_text(encoding="utf-8")
        web_gui = (root / "sprocket_mod_manager" / "web_gui.py").read_text(
            encoding="utf-8"
        )
        html = (
            root / "sprocket_mod_manager" / "client_ui" / "index.html"
        ).read_text(encoding="utf-8")
        javascript = (
            root / "sprocket_mod_manager" / "client_ui" / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("from sprocket_mod_manager.web_gui import run_gui", entry)
        self.assertNotIn("tkinter", web_gui)
        self.assertNotIn("customtkinter", web_gui)
        self.assertEqual(html.count('id="language-select"'), 1)
        self.assertIn('id="modal-layer" hidden', html)
        self.assertIn('id="melonloader-action"', html)
        self.assertIn('id="text-scale"', html)
        self.assertNotIn("window.alert", javascript)
        self.assertNotIn("window.confirm", javascript)
        self.assertIn("async function ensureMelonLoader", javascript)
        self.assertIn("function sanitizeReadmeHtml", javascript)
        self.assertIn('callApi("get_package_readme"', javascript)
        self.assertIn("metadata.textContent = localized(pkg.description, pkg.id)", javascript)
        self.assertIn('heading.className = "detail-heading"', javascript)
        self.assertIn('readmeSection.className = "detail-readme"', javascript)
        self.assertIn(
            "panel.append(topline, heading, actions, readmeSection, facts, dependencySection, recommendationSection)",
            javascript,
        )
        self.assertIn('"enqueue_install",', javascript)
        self.assertIn("loaderDecision.allowWithout", javascript)
        self.assertIn("function applyTextScale", javascript)
        self.assertIn("checkbox.checked = false", javascript)
        self.assertIn("result.recommendations || []", javascript)
        self.assertIn("if (featured) return featured", javascript)
        self.assertIn('star.textContent = "★"', javascript)
        self.assertIn('tr("starterRecommended")', javascript)
        self.assertIn("function showStarterRecommendations()", javascript)
        self.assertIn("state.unrecognized = result.unrecognized || []", javascript)
        self.assertIn('status.textContent = tr("unrecognized")', javascript)
        self.assertIn("if (item.unrecognized)", javascript)
        self.assertNotIn("recommendedOptional", javascript)
        self.assertNotIn("recommendation-hint", javascript)
        self.assertNotIn("empty-glyph", html)
        self.assertIn('class="brand-line" aria-hidden="true"', html)
        self.assertNotIn("about-mark", html)

    def test_catalog_columns_share_one_bounded_scroll_area(self):
        css = (
            Path(__file__).parents[1]
            / "sprocket_mod_manager"
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
        root = Path(__file__).parents[1] / "sprocket_mod_manager" / "client_ui"
        html = (root / "index.html").read_text(encoding="utf-8")
        javascript = (root / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-page-target="translations"', html)
        self.assertIn('id="page-translations" data-page="translations"', html)
        self.assertIn('id="translation-list"', html)
        self.assertNotIn('<option value="translation"', html)
        self.assertIn('pkg.category === "translation") !== translations', javascript)
        self.assertIn('translations: { kicker: "LOCALIZATION"', javascript)

    def test_native_select_options_use_the_dark_palette(self):
        css = (
            Path(__file__).parents[1]
            / "sprocket_mod_manager"
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
            root / "sprocket_mod_manager" / "client_ui" / "app.css"
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


if __name__ == "__main__":
    unittest.main()
