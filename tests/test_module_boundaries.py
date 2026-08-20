from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "sprocket_mod_manager"
PRESENTATION = PACKAGE / "presentation"
INFRASTRUCTURE = PACKAGE / "infrastructure"
APPLICATION = PACKAGE / "application"


class ModuleBoundaryTests(unittest.TestCase):
    def test_web_api_does_not_own_desktop_window_creation(self) -> None:
        source = (PRESENTATION / "web_gui.py").read_text(encoding="utf-8")
        self.assertNotIn("webview.create_window", source)
        self.assertNotIn("webview.start(", source)

    def test_webview_host_only_depends_on_api_at_runtime(self) -> None:
        tree = ast.parse((PRESENTATION / "webview_app.py").read_text(encoding="utf-8"))
        top_level_imports = [
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertNotIn("web_gui", top_level_imports)

    def test_debug_mode_reaches_logging_and_webview(self) -> None:
        entry = (ROOT / "modman.py").read_text(encoding="utf-8")
        host = (PRESENTATION / "webview_app.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--debug"', entry)
        self.assertIn("configure_logging(app_dir, debug=debug", entry)
        self.assertIn("debug=debug", host)

    def test_presentation_api_exports_remain_available(self) -> None:
        from sprocket_mod_manager.presentation.web_gui import ClientApi
        from sprocket_mod_manager.presentation.webview_app import run_gui

        self.assertTrue(callable(ClientApi))
        self.assertTrue(callable(run_gui))

    def test_config_does_not_import_application_service(self) -> None:
        tree = ast.parse((INFRASTRUCTURE / "config.py").read_text(encoding="utf-8"))
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        self.assertNotIn("service", imports)

    def test_service_delegates_registry_and_profile_infrastructure(self) -> None:
        source = (APPLICATION / "service.py").read_text(encoding="utf-8")
        self.assertIn("RegistrySourceLoader", source)
        self.assertIn("InstallerProfiles", source)
        self.assertNotIn("json.loads", source)
        self.assertNotIn("hashlib.sha256", source)

    def test_package_root_contains_no_business_modules(self) -> None:
        root_modules = {path.name for path in PACKAGE.glob("*.py")}
        self.assertEqual(root_modules, {"__init__.py"})

    def test_legacy_tk_presentation_is_removed(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
        presentation_sources = "\n".join(
            path.read_text(encoding="utf-8").casefold()
            for path in PRESENTATION.rglob("*.py")
        )
        self.assertNotIn("customtkinter", requirements)
        self.assertNotIn("import tkinter", presentation_sources)
        self.assertFalse((PRESENTATION / "gui.py").exists())
        self.assertFalse((PRESENTATION / "dialogs.py").exists())

    def test_responsibility_packages_are_explicit(self) -> None:
        package_names = {
            path.name
            for path in PACKAGE.iterdir()
            if path.is_dir() and (path / "__init__.py").is_file()
        }
        self.assertEqual(
            package_names,
            {"application", "domain", "infrastructure", "presentation", "utilities"},
        )

    def test_dependency_direction_is_enforced(self) -> None:
        forbidden = {
            "domain": ("application", "infrastructure", "presentation"),
            "application": ("presentation",),
            "infrastructure": ("application", "presentation"),
            "utilities": ("application", "infrastructure", "presentation"),
        }
        for package_name, prefixes in forbidden.items():
            for source_path in (PACKAGE / package_name).rglob("*.py"):
                tree = ast.parse(source_path.read_text(encoding="utf-8"))
                imported = {
                    node.module or ""
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                }
                violations = sorted(
                    module
                    for module in imported
                    if module.startswith(prefixes)
                )
                self.assertEqual(violations, [], str(source_path.relative_to(ROOT)))


if __name__ == "__main__":
    unittest.main()
