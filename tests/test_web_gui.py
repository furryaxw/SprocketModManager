import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.config import ConfigStore
from sprocket_mod_manager.github import RepositoryReadme
from sprocket_mod_manager.melonloader import MelonLoaderInstallation
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


class WebGuiTests(unittest.TestCase):
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
                        "text_scale": 150,
                    }
                )
                loaded = api.get_settings()
            finally:
                api.install_queue.close()

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["settings"]["text_scale"], 150)
        self.assertEqual(loaded["settings"]["text_scale"], 150)

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
            "panel.append(topline, heading, actions, readmeSection, facts, dependencySection)",
            javascript,
        )
        self.assertIn('"enqueue_install",', javascript)
        self.assertIn("loaderDecision.allowWithout", javascript)
        self.assertIn("function applyTextScale", javascript)

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
