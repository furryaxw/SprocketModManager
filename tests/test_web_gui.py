import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sprocket_mod_manager.config import ConfigStore
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
        self.assertNotIn("window.alert", javascript)
        self.assertNotIn("window.confirm", javascript)

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
