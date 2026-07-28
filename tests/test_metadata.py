import importlib.util
import unittest
from pathlib import Path

from sprocket_mod_manager.models import RegistryPackage


def load_index_module():
    path = Path(__file__).parents[1] / "gen-index.py"
    spec = importlib.util.spec_from_file_location("sprocket_gen_index_tests", path)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load gen-index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INDEX = load_index_module()


def metadata():
    return {
        "schema_version": 1,
        "id": "example.mod",
        "name": "ExampleMod",
        "authors": ["ExampleAuthor"],
        "repository": "ExampleAuthor/ExampleMod",
        "license": "MIT",
        "display_name": {"ja": "サンプル Mod"},
        "release": {
            "include_prerelease": False,
            "version_pattern": r"^v?(\d+\.\d+\.\d+)$",
            "assets": {"include": ["*.dll"], "exclude": []},
        },
        "dependencies": [],
        "install": {"scan_dlls": True, "exclude": [], "overrides": []},
        "category": "utility",
        "tags": ["example"],
    }


class MetadataLocalizationTests(unittest.TestCase):
    def test_one_display_language_without_description_is_valid(self):
        INDEX.validate_meta(metadata(), "example.mod")

    def test_description_languages_are_independent(self):
        meta = metadata()
        meta["description"] = {"pt-BR": "Descricao opcional."}
        INDEX.validate_meta(meta, "example.mod")

    def test_open_language_tags_are_valid(self):
        meta = metadata()
        meta["display_name"] = {
            "pt-BR": "Exemplo",
            "zh-Hans": "示例",
            "X-sprocket-test": "Private translation",
        }
        INDEX.validate_meta(meta, "example.mod")

    def test_invalid_language_tags_are_rejected(self):
        for tag in ("e", "english_US", "en--US", "-en", "en-abcdefghi"):
            with self.subTest(tag=tag):
                meta = metadata()
                meta["display_name"] = {tag: "Example"}
                with self.assertRaisesRegex(INDEX.RegistryError, "invalid language tag"):
                    INDEX.validate_meta(meta, "example.mod")

    def test_language_tags_are_unique_ignoring_case(self):
        meta = metadata()
        meta["display_name"] = {"pt-BR": "Exemplo", "pt-br": "Duplicado"}
        with self.assertRaisesRegex(INDEX.RegistryError, "duplicate language tag"):
            INDEX.validate_meta(meta, "example.mod")

    def test_empty_display_name_is_rejected(self):
        meta = metadata()
        meta["display_name"] = {}
        with self.assertRaisesRegex(INDEX.RegistryError, "display_name must be a non-empty"):
            INDEX.validate_meta(meta, "example.mod")

    def test_empty_description_is_rejected_when_present(self):
        meta = metadata()
        meta["description"] = {}
        with self.assertRaisesRegex(INDEX.RegistryError, "description must be a non-empty"):
            INDEX.validate_meta(meta, "example.mod")

    def test_model_localization_fallbacks(self):
        package = RegistryPackage.from_dict(metadata())
        self.assertEqual(package.label("ja-JP"), "サンプル Mod")
        self.assertEqual(package.label("fr"), "サンプル Mod")

        english = RegistryPackage.from_dict({**metadata(), "display_name": {"ja": "サンプル", "en-US": "Example"}})
        self.assertEqual(english.label("fr"), "Example")

        assembly = RegistryPackage.from_dict({**metadata(), "display_name": {}})
        self.assertEqual(assembly.label("en"), "ExampleMod")


if __name__ == "__main__":
    unittest.main()
