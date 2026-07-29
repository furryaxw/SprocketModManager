import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_generated_index_embeds_normalized_releases(self):
        release = {
            "id": 42,
            "tag": "v1.2.3",
            "version": "1.2.3",
            "prerelease": False,
            "published_at": "2026-07-29T00:00:00Z",
            "page_url": "https://github.com/ExampleAuthor/ExampleMod/releases/tag/v1.2.3",
            "assets": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_dir = root / "mods" / "example.mod"
            package_dir.mkdir(parents=True)
            (package_dir / "sprocket-mod.json").write_text(
                json.dumps(metadata()),
                encoding="utf-8",
            )
            output = root / "index.json"

            index = INDEX.generate_index(
                root / "mods",
                output,
                release_loader=lambda _package: [release],
            )

        self.assertEqual(index["packages"][0]["releases"], [release])

    def test_release_fetch_falls_back_to_latest_when_list_has_no_compatible_asset(self):
        invalid = {
            "id": 41,
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/ExampleAuthor/ExampleMod/releases/tag/v1.2.3",
            "assets": [],
        }
        latest = {
            **invalid,
            "id": 42,
            "assets": [
                {
                    "id": 7,
                    "name": "ExampleMod.dll",
                    "size": 123,
                    "browser_download_url": (
                        "https://github.com/ExampleAuthor/ExampleMod/releases/"
                        "download/v1.2.3/ExampleMod.dll"
                    ),
                    "updated_at": "2026-07-29T00:00:00Z",
                }
            ],
        }
        responses = [[invalid], latest]

        with patch.object(INDEX, "_github_json", side_effect=responses) as request:
            releases = INDEX.fetch_package_releases(metadata())

        self.assertEqual(releases[0]["assets"][0]["name"], "ExampleMod.dll")
        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            [
                "/repos/ExampleAuthor/ExampleMod/releases?per_page=100",
                "/repos/ExampleAuthor/ExampleMod/releases/latest",
            ],
        )

    def test_model_reads_embedded_releases(self):
        raw = {
            **metadata(),
            "releases": [
                {
                    "id": 42,
                    "tag": "v1.2.3",
                    "version": "1.2.3",
                    "prerelease": False,
                    "published_at": "2026-07-29T00:00:00Z",
                    "page_url": "https://github.com/ExampleAuthor/ExampleMod/releases/tag/v1.2.3",
                    "assets": [
                        {
                            "id": 7,
                            "name": "ExampleMod.dll",
                            "size": 123,
                            "download_url": "https://github.com/ExampleAuthor/ExampleMod/releases/download/v1.2.3/ExampleMod.dll",
                            "digest": "sha256:abc",
                            "updated_at": "2026-07-29T00:00:00Z",
                        }
                    ],
                }
            ],
        }

        package = RegistryPackage.from_dict(raw)

        self.assertIsNotNone(package.releases)
        self.assertEqual(str(package.releases[0].version), "1.2.3")
        self.assertEqual(package.releases[0].assets[0].name, "ExampleMod.dll")

    def test_model_rejects_embedded_asset_from_another_repository(self):
        raw = {
            **metadata(),
            "releases": [
                {
                    "id": 42,
                    "tag": "v1.2.3",
                    "version": "1.2.3",
                    "prerelease": False,
                    "published_at": "2026-07-29T00:00:00Z",
                    "page_url": "https://github.com/ExampleAuthor/ExampleMod/releases/tag/v1.2.3",
                    "assets": [
                        {
                            "id": 7,
                            "name": "ExampleMod.dll",
                            "size": 123,
                            "download_url": "https://github.com/attacker/Other/releases/download/v1.2.3/ExampleMod.dll",
                        }
                    ],
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "release asset URL"):
            RegistryPackage.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
