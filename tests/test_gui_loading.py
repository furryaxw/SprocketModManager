import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sprocket_mod_manager.gui import (
    COLORS,
    MANAGER_REPOSITORY_URL,
    REGISTRY_WEBSITE_URL,
    ModManagerApp,
    _bind_click_tree,
    _set_primary_button_enabled,
    load_catalog,
)
from sprocket_mod_manager.github import RepositoryRelease
from sprocket_mod_manager.semver import Version


class FakeGitHub:
    def __init__(self):
        self.barrier = threading.Barrier(2, timeout=2)
        self.refresh_values = []

    def releases(self, package, refresh=False):
        self.refresh_values.append(refresh)
        self.barrier.wait()
        return (SimpleNamespace(package_id=package.id),)

    @staticmethod
    def install_assets(_package, _release):
        return (object(),)


class FakeService:
    def __init__(self):
        self.github = FakeGitHub()
        self.registry = SimpleNamespace(
            packages=(SimpleNamespace(id="test.one"), SimpleNamespace(id="test.two"))
        )
        self.registry_refresh = None

    def load_registry(self, _source, refresh=False):
        self.registry_refresh = refresh
        return self.registry


class CatalogLoadingTests(unittest.TestCase):
    @staticmethod
    def _contrast(left, right):
        def luminance(value):
            channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                channel / 12.92
                if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        brighter, darker = sorted((luminance(left), luminance(right)), reverse=True)
        return (brighter + 0.05) / (darker + 0.05)

    def test_primary_button_palette_has_readable_text_contrast(self):
        self.assertGreaterEqual(
            self._contrast(COLORS["button_accent"], COLORS["text"]),
            4.5,
        )
        self.assertGreaterEqual(
            self._contrast(COLORS["button_accent_hover"], COLORS["text"]),
            4.5,
        )

    def test_disabled_primary_button_uses_neutral_surface(self):
        class Button:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        button = Button()
        _set_primary_button_enabled(button, False)

        self.assertEqual(button.options["state"], "disabled")
        self.assertEqual(button.options["fg_color"], COLORS["surface_high"])

    def test_update_status_and_about_button_use_separate_translations(self):
        from sprocket_mod_manager.gui import TEXT

        self.assertEqual(TEXT["zh"]["manager_up_to_date"], "已是最新")
        self.assertEqual(TEXT["zh"]["all_mods_up_to_date"], "没有可用更新")

    def test_about_page_is_registered(self):
        built = []
        app = SimpleNamespace(pages={}, _build_about=lambda: built.append("about"))

        ModManagerApp._ensure_page(app, "about")

        self.assertEqual(built, ["about"])

    def test_about_links_open_expected_project_pages(self):
        with patch("sprocket_mod_manager.gui.webbrowser.open") as open_browser:
            ModManagerApp.open_manager_repository(SimpleNamespace())
            ModManagerApp.open_registry_website(SimpleNamespace())

        self.assertEqual(
            open_browser.call_args_list,
            [
                unittest.mock.call(MANAGER_REPOSITORY_URL),
                unittest.mock.call(REGISTRY_WEBSITE_URL),
            ],
        )

    def test_about_page_shows_latest_version_and_one_update_action(self):
        class Widget:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        release = RepositoryRelease(
            tag="v0.2.0",
            version=Version.parse("0.2.0"),
            page_url="https://github.com/furryaxw/sprocket-mods/releases/tag/v0.2.0",
        )
        latest = Widget()
        update = Widget()
        app = SimpleNamespace(
            pages={"about": object()},
            version="0.1.1",
            about_latest_version=latest,
            about_update_button=update,
            tr=lambda key: key,
            refresh_about_release=lambda: None,
        )

        ModManagerApp._about_release_loaded(app, release)

        self.assertEqual(latest.options["text"], "0.2.0")
        self.assertEqual(update.options["text"], "view_update")
        self.assertEqual(update.options["state"], "normal")
        with patch("sprocket_mod_manager.gui.webbrowser.open") as open_browser:
            update.options["command"]()
        open_browser.assert_called_once_with(release.page_url)

    def test_catalog_uses_cache_and_loads_releases_concurrently(self):
        service = FakeService()
        loaded_service, latest = load_catalog(service, "index.json", refresh=False)
        self.assertIs(loaded_service, service)
        self.assertFalse(service.registry_refresh)
        self.assertEqual(service.github.refresh_values, [False, False])
        self.assertEqual(set(latest), {"test.one", "test.two"})

    def test_click_binding_covers_every_nested_widget(self):
        calls = []

        class Widget:
            def __init__(self, *children):
                self.children = children
                self.binding = None

            def bind(self, event, callback, add=None):
                self.binding = (event, callback, add)

            def winfo_children(self):
                return self.children

        leaf = Widget()
        child = Widget(leaf)
        root = Widget(child)
        _bind_click_tree(root, lambda: calls.append("selected"))

        for widget in (root, child, leaf):
            event, callback, add = widget.binding
            self.assertEqual(event, "<Button-1>")
            self.assertEqual(add, "+")
            callback(None)
        self.assertEqual(calls, ["selected", "selected", "selected"])

    def test_background_failure_preserves_exception_for_ui_callback(self):
        class ImmediateThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                self.target()

        class FakeApp:
            busy = False

            def __init__(self):
                self.scheduled = []
                self.errors = []

            def _set_busy(self, value):
                self.busy = value

            def after(self, _delay, callback):
                self.scheduled.append(callback)

            def _task_failed(self, error):
                self.errors.append(error)

        expected = RuntimeError("install failed")

        def fail():
            raise expected

        app = FakeApp()
        with patch("sprocket_mod_manager.gui.threading.Thread", ImmediateThread):
            ModManagerApp._background(app, fail, lambda _result: None)

        self.assertEqual(len(app.scheduled), 1)
        app.scheduled[0]()
        self.assertEqual(app.errors, [expected])

    def test_startup_update_check_offers_newer_manager_release(self):
        class ImmediateThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                self.target()

        class GitHub:
            def __init__(self, release):
                self.release = release
                self.repositories = []

            def latest_repository_release(self, repository):
                self.repositories.append(repository)
                return self.release

        class FakeApp:
            version = "0.1.0"

            def __init__(self, release):
                self.github = GitHub(release)
                self.service = SimpleNamespace(github=self.github)
                self.scheduled = []
                self.offered = []

            def after(self, _delay, callback):
                self.scheduled.append(callback)

            def _offer_update(self, release):
                self.offered.append(release)

        release = RepositoryRelease(
            tag="v0.2.0",
            version=Version.parse("0.2.0"),
            page_url="https://github.com/furryaxw/sprocket-mods/releases/tag/v0.2.0",
        )
        app = FakeApp(release)
        with patch("sprocket_mod_manager.gui.threading.Thread", ImmediateThread):
            ModManagerApp.check_for_updates(app)

        self.assertEqual(app.github.repositories, ["furryaxw/sprocket-mods"])
        self.assertEqual(len(app.scheduled), 1)
        app.scheduled[0]()
        self.assertEqual(app.offered, [release])

    def test_startup_update_check_ignores_current_release(self):
        class ImmediateThread:
            def __init__(self, *, target, daemon):
                self.target = target

            def start(self):
                self.target()

        release = RepositoryRelease(
            tag="v0.1.0",
            version=Version.parse("0.1.0"),
            page_url="https://github.com/furryaxw/sprocket-mods/releases/tag/v0.1.0",
        )
        app = SimpleNamespace(
            version="0.1.0",
            service=SimpleNamespace(
                github=SimpleNamespace(latest_repository_release=lambda _repository: release)
            ),
            after=lambda *_args: self.fail("current release should not schedule a prompt"),
        )
        with patch("sprocket_mod_manager.gui.threading.Thread", ImmediateThread):
            ModManagerApp.check_for_updates(app)

    def test_update_prompt_opens_validated_release_page_after_confirmation(self):
        release = RepositoryRelease(
            tag="v0.2.0",
            version=Version.parse("0.2.0"),
            page_url="https://github.com/furryaxw/sprocket-mods/releases/tag/v0.2.0",
        )

        class FakeApp:
            version = "0.1.0"

            @staticmethod
            def tr(key, **values):
                return f"{key}:{values}" if values else key

        with (
            patch("sprocket_mod_manager.gui.ask_confirmation", return_value=True),
            patch("sprocket_mod_manager.gui.webbrowser.open") as open_browser,
        ):
            ModManagerApp._offer_update(FakeApp(), release)

        open_browser.assert_called_once_with(release.page_url)

    def test_settings_page_reloads_saved_config_every_time_it_is_opened(self):
        class ConfigStore:
            def __init__(self):
                self.loads = 0

            def load(self):
                self.loads += 1
                return {
                    "language": "auto",
                    "game_path": "G:/Saved/Sprocket",
                    "index_url": "https://saved.example/index.json",
                }

        class Page:
            def __init__(self, snapshot=None):
                self.snapshot = snapshot
                self.destroyed = False
                self.raised = 0

            def destroy(self):
                self.destroyed = True

            def tkraise(self):
                self.raised += 1

        class Button:
            def configure(self, **_kwargs):
                pass

        class FakeApp:
            def __init__(self):
                self.config_store = ConfigStore()
                self.config = {}
                self.pages = {"browse": Page()}
                self.nav_buttons = {"browse": Button(), "settings": Button()}
                self.current_tab = "browse"
                self.busy = False
                self.selected = None
                self.service = SimpleNamespace(registry=None)
                self.built_settings = []

            def _ensure_page(self, name):
                if name not in self.pages:
                    page = Page(dict(self.config))
                    self.pages[name] = page
                    if name == "settings":
                        self.built_settings.append(page)

            def _set_busy(self, _value):
                pass

            def populate_installed(self):
                pass

        app = FakeApp()
        ModManagerApp.show_page(app, "settings")
        first_page = app.pages["settings"]
        first_page.draft = "unsaved edit"

        ModManagerApp.show_page(app, "browse")
        ModManagerApp.show_page(app, "settings")

        self.assertEqual(app.config_store.loads, 2)
        self.assertTrue(first_page.destroyed)
        self.assertIsNot(app.pages["settings"], first_page)
        self.assertEqual(
            app.pages["settings"].snapshot["game_path"],
            "G:/Saved/Sprocket",
        )
