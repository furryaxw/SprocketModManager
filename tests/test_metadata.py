import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from sprocket_mod_manager.domain.models import RegistryPackage
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.domain.errors import RegistryError


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

    def test_recommendations_are_optional_and_must_be_unique_package_ids(self):
        INDEX.validate_meta(metadata(), "example.mod")

        for recommendations in (
            "example.other",
            ["invalid"],
            [{"id": "example.other"}],
            ["example.other", "example.other"],
            ["example.mod"],
        ):
            with self.subTest(recommendations=recommendations):
                meta = metadata()
                meta["recommendations"] = recommendations
                with self.assertRaises(INDEX.RegistryError):
                    INDEX.validate_meta(meta, "example.mod")

    def test_featured_is_an_optional_boolean(self):
        INDEX.validate_meta(metadata(), "example.mod")
        featured = {**metadata(), "featured": True}
        INDEX.validate_meta(featured, "example.mod")
        self.assertTrue(RegistryPackage.from_dict(featured).featured)
        self.assertFalse(RegistryPackage.from_dict(metadata()).featured)

        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value):
                invalid = {**metadata(), "featured": value}
                with self.assertRaisesRegex(INDEX.RegistryError, "featured must be a boolean"):
                    INDEX.validate_meta(invalid, "example.mod")
                with self.assertRaisesRegex(TypeError, "featured must be a boolean"):
                    RegistryPackage.from_dict(invalid)

    def test_registry_requires_recommendations_to_reference_registered_packages(self):
        root_data = {**metadata(), "recommendations": ["example.companion"]}
        with self.assertRaisesRegex(RegistryError, "recommendation is not registered"):
            Registry.from_dict({"schema_version": 1, "packages": [root_data]})

        companion_data = {
            **metadata(),
            "id": "example.companion",
            "name": "ExampleCompanion",
            "repository": "ExampleAuthor/ExampleCompanion",
        }
        root = RegistryPackage.from_dict(root_data)
        companion = RegistryPackage.from_dict(companion_data)
        registry = Registry([root, companion])
        self.assertEqual(registry.get(root.id).recommendations, (companion.id,))

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
                release_loader=lambda _package, _known, _axes: [release],
            )

        self.assertEqual(index["packages"][0]["releases"], [release])

    def test_github_release_field_controls_prerelease_filtering(self):
        meta = metadata()
        meta["release"]["version_pattern"] = (
            r"^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$"
        )
        record = {
            "id": 42,
            "tag_name": "v0.2.0-fix1",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-07-29T00:00:00Z",
            "html_url": (
                "https://github.com/ExampleAuthor/ExampleMod/releases/tag/v0.2.0-fix1"
            ),
            "assets": [
                {
                    "id": 7,
                    "name": "ExampleMod.dll",
                    "size": 123,
                    "browser_download_url": (
                        "https://github.com/ExampleAuthor/ExampleMod/releases/"
                        "download/v0.2.0-fix1/ExampleMod.dll"
                    ),
                    "updated_at": "2026-07-29T00:00:00Z",
                }
            ],
        }

        releases = INDEX.normalize_release_records(meta, [record])

        self.assertEqual([release["tag"] for release in releases], ["v0.2.0-fix1"])
        self.assertFalse(releases[0]["prerelease"])

    def test_github_json_retries_transient_errors(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        with (
            patch.object(INDEX, "urlopen", side_effect=[URLError("temporary"), response]) as opener,
            patch.object(INDEX.json, "load", return_value={"ok": True}),
            patch.object(INDEX.time, "sleep") as sleep,
        ):
            result = INDEX._github_json("/repos/example/mod")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_generated_index_restores_releases_after_package_fetch_failure(self):
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
            with (
                patch.object(
                    INDEX,
                    "load_index_releases",
                    return_value={"example.mod": [release]},
                ) as fallback,
                patch("builtins.print") as warning,
            ):
                index = INDEX.generate_index(
                    root / "mods",
                    root / "index.json",
                    release_loader=unittest.mock.Mock(
                        side_effect=INDEX.RegistryError("temporary failure")
                    ),
                    fallback_index_url="https://example.com/index.json",
                )

        self.assertEqual(index["packages"][0]["releases"], [release])
        fallback.assert_called_once_with("https://example.com/index.json")
        messages = [call.args[0] for call in warning.call_args_list if call.args]
        self.assertTrue(
            any("restored releases" in message for message in messages),
            messages,
        )

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


class InstallTypeValidationTests(unittest.TestCase):
    """安装类型可以写成具体类型或通配；供给位置必须具体；外部下载来源只有 modloader 可以声明。"""

    def schema_two(self) -> dict:
        meta = metadata()
        meta["schema_version"] = 2
        meta["install"] = {
            "files": [{"match": "*.dll", "type": "melonloader:mod"}],
            "scan_dlls": True,
            "exclude": [],
        }
        return meta

    def test_install_file_types_may_be_wildcards(self):
        meta = self.schema_two()
        meta["install"]["files"] = [{"match": "*.dll", "type": "melonloader:*"}]

        INDEX.validate_meta(meta, "example.mod")

    def test_install_file_types_must_be_well_formed(self):
        meta = self.schema_two()
        for value in ("", "melonloader", ":mod", "melonloader:"):
            with self.subTest(value=value):
                meta["install"]["files"] = [{"match": "*.dll", "type": value}]
                with self.assertRaisesRegex(INDEX.RegistryError, "invalid install file type"):
                    INDEX.validate_meta(meta, "example.mod")

    def test_supply_keys_must_be_concrete_types(self):
        meta = self.schema_two()
        meta["kind"] = "modloader"
        meta["supply"] = {"melonloader:*": "{Sprocket}/Mods"}

        with self.assertRaisesRegex(INDEX.RegistryError, "invalid supplied type"):
            INDEX.validate_meta(meta, "example.mod")

    def test_install_types_are_matched_by_concrete_type(self):
        packages = {
            "example.mod": {
                "supply": {"melonloader:mod": "{Sprocket}/Mods"},
                "install": {"files": [{"match": "*.dll", "type": "melonloader:plugin"}]},
            }
        }

        with self.assertRaisesRegex(INDEX.RegistryError, "not supplied"):
            INDEX.validate_install_types(packages)

        packages["example.mod"]["install"]["files"] = [
            {"match": "*.dll", "type": "melonloader:mod"}
        ]
        INDEX.validate_install_types(packages)

    def test_a_wildcard_install_type_needs_a_supplier_in_its_namespace(self):
        packages = {
            "example.mod": {
                "supply": {"melonloader:mod": "{Sprocket}/Mods"},
                "install": {"files": [{"match": "*.dll", "type": "melonloader:*"}]},
            }
        }

        INDEX.validate_install_types(packages)

        packages["example.mod"]["install"]["files"] = [
            {"match": "*.dll", "type": "bepinex:*"}
        ]
        with self.assertRaisesRegex(INDEX.RegistryError, "not supplied"):
            INDEX.validate_install_types(packages)

    def test_only_a_modloader_may_declare_an_external_source(self):
        meta = metadata()
        meta["release"]["source"] = {"type": "external", "hosts": ["example.org"]}

        with self.assertRaisesRegex(
            INDEX.RegistryError, "only a modloader may declare an external release source"
        ):
            INDEX.validate_meta(meta, "example.mod")

        loader = self.schema_two()
        loader["kind"] = "modloader"
        loader["supply"] = {"melonloader:mod": "{Sprocket}/Mods"}
        loader["releases"] = [{"id": 1}]
        loader["release"]["source"] = {"type": "external", "hosts": ["example.org"]}

        INDEX.validate_meta(loader, "example.mod")


class KindValidationTests(unittest.TestCase):
    def schema_two(self) -> dict:
        meta = metadata()
        meta["schema_version"] = 2
        meta["install"] = {
            "files": [{"match": "*.dll", "type": "melonloader:mod"}],
            "scan_dlls": True,
            "exclude": [],
        }
        return meta

    def test_kind_defaults_to_modfile(self):
        meta = self.schema_two()
        INDEX.validate_meta(meta, "example.mod")
        self.assertEqual(RegistryPackage.from_dict(meta).kind, "modfile")
        self.assertFalse(RegistryPackage.from_dict(meta).is_loader)

    def test_unknown_kinds_are_rejected(self):
        meta = {**self.schema_two(), "kind": "plugin"}
        with self.assertRaisesRegex(INDEX.RegistryError, "invalid kind"):
            INDEX.validate_meta(meta, "example.mod")
        with self.assertRaisesRegex(ValueError, "unknown package kind"):
            RegistryPackage.from_dict(meta)

    def test_a_loader_kind_may_use_the_type_line(self):
        meta = {**self.schema_two(), "kind": "patch", "install": {
            "mode": "patch",
            "files": [{"match": "Patch/*.dll", "type": "melonloader:mod"}],
            "scan_dlls": False,
            "exclude": [],
        }}
        INDEX.validate_meta(meta, "example.mod")

    def test_a_loader_kind_may_use_the_payload_line(self):
        meta = {
            **self.schema_two(),
            "kind": "modloader",
            "supply": {"melonloader:mod": "{Sprocket}/Mods"},
            "install": {"payload": [{"match": "**", "target": "{Sprocket}", "layout": "tree"}], "exclude": []},
        }
        INDEX.validate_meta(meta, "example.mod")
        self.assertTrue(RegistryPackage.from_dict(meta).uses_payload)

    def test_a_modfile_may_not_use_the_payload_line(self):
        meta = {**self.schema_two(), "install": {"payload": [{"match": "**", "target": "{Sprocket}"}], "exclude": []}}
        with self.assertRaisesRegex(INDEX.RegistryError, "modfile installs through install.files"):
            INDEX.validate_meta(meta, "example.mod")

    def test_the_two_install_lines_may_not_be_mixed(self):
        meta = {
            **self.schema_two(),
            "kind": "modloader",
            "supply": {"melonloader:mod": "{Sprocket}/Mods"},
            "install": {
                "files": [{"match": "*.dll", "type": "melonloader:mod"}],
                "payload": [{"match": "**", "target": "{Sprocket}"}],
                "scan_dlls": False,
                "exclude": [],
            },
        }
        with self.assertRaisesRegex(INDEX.RegistryError, "must not mix"):
            INDEX.validate_meta(meta, "example.mod")

    def test_a_payload_target_must_be_a_game_root_path(self):
        meta = {
            **self.schema_two(),
            "kind": "modloader",
            "supply": {"melonloader:mod": "{Sprocket}/Mods"},
            "install": {"payload": [{"match": "**", "target": "Mods"}], "exclude": []},
        }
        with self.assertRaisesRegex(INDEX.RegistryError, "invalid install payload target"):
            INDEX.validate_meta(meta, "example.mod")

    def test_replace_requires_patch_mode(self):
        meta = {
            **self.schema_two(),
            "install": {
                "files": [{"match": "**", "type": "xunity:translation"}],
                "replace": ["xunity:translation"],
                "scan_dlls": False,
                "exclude": [],
            },
        }
        with self.assertRaisesRegex(INDEX.RegistryError, "requires install.mode patch"):
            INDEX.validate_meta(meta, "example.mod")

    def test_replace_requires_a_files_rule_for_the_type(self):
        meta = {
            **self.schema_two(),
            "install": {
                "mode": "patch",
                "files": [{"match": "**", "type": "melonloader:mod"}],
                "replace": ["xunity:translation"],
                "scan_dlls": False,
                "exclude": [],
            },
        }
        with self.assertRaisesRegex(INDEX.RegistryError, "needs a files rule"):
            INDEX.validate_meta(meta, "example.mod")

    def test_replace_is_valid_on_the_type_line(self):
        meta = {
            **self.schema_two(),
            "kind": "patch",
            "install": {
                "mode": "patch",
                "files": [{"match": "**", "type": "xunity:translation"}],
                "replace": ["xunity:translation"],
                "scan_dlls": False,
                "exclude": [],
            },
        }
        INDEX.validate_meta(meta, "example.mod")
        self.assertEqual(
            RegistryPackage.from_dict(meta).replace_types(), ("xunity:translation",)
        )


class ProvidesValidationTests(unittest.TestCase):
    def schema_two(self) -> dict:
        meta = metadata()
        meta["schema_version"] = 2
        meta["install"] = {
            "files": [{"match": "*.dll", "type": "melonloader:mod"}],
            "scan_dlls": True,
            "exclude": [],
        }
        return meta

    def test_provides_maps_a_capability_to_a_version(self):
        meta = {**self.schema_two(), "provides": {"lavagang.melonloader": "0.7.3"}}
        INDEX.validate_meta(meta, "example.mod")

    def test_the_version_template_is_accepted(self):
        meta = {**self.schema_two(), "provides": {"bepinex.bepinex": "{version}"}}
        INDEX.validate_meta(meta, "example.mod")

    def test_provides_values_must_be_versions(self):
        meta = {**self.schema_two(), "provides": {"lavagang.melonloader": "not-a-version"}}
        with self.assertRaisesRegex(INDEX.RegistryError, "invalid version"):
            INDEX.validate_meta(meta, "example.mod")

    def test_provides_keys_must_be_capability_ids(self):
        meta = {**self.schema_two(), "provides": {"LavaGang": "0.7.3"}}
        with self.assertRaisesRegex(INDEX.RegistryError, "invalid provided capability id"):
            INDEX.validate_meta(meta, "example.mod")

    def test_absent_provides_defaults_to_the_own_id(self):
        modfile = RegistryPackage.from_dict(self.schema_two())
        self.assertEqual(modfile.declared_capabilities(), {})
        self.assertEqual(modfile.capabilities(), {"example.mod": "{version}"})

        loader = RegistryPackage.from_dict(
            {
                **self.schema_two(),
                "kind": "modloader",
                "supply": {"melonloader:mod": "{Sprocket}/Mods"},
                "install": {"payload": [{"match": "**", "target": "{Sprocket}"}], "exclude": []},
            }
        )
        self.assertEqual(loader.declared_capabilities(), {})
        self.assertEqual(loader.capabilities(), {"example.mod": "{version}"})

    def test_explicit_provides_replaces_the_default(self):
        meta = {
            **self.schema_two(),
            "kind": "modloader",
            "supply": {"bepinex:mod": "{Sprocket}/Mods"},
            "install": {"payload": [{"match": "**", "target": "{Sprocket}"}], "exclude": []},
            "provides": {"bepinex.bepinex": "{version}"},
        }
        package = RegistryPackage.from_dict(meta)
        self.assertEqual(package.capabilities(), {"bepinex.bepinex": "{version}"})


class CapabilityDependencyTests(unittest.TestCase):
    def test_a_dependency_on_an_unprovided_capability_is_rejected(self):
        data = {**metadata(), "dependencies": [{"id": "nobody.provides", "version": "*", "when": "*"}]}
        with self.assertRaisesRegex(RegistryError, "dependency is not registered"):
            Registry.from_dict({"schema_version": 1, "packages": [data]})

    def test_a_dependency_on_the_game_capability_is_allowed(self):
        data = {**metadata(), "dependencies": [{"id": "hamish.sprocket", "version": "*", "when": "*"}]}
        registry = Registry.from_dict({"schema_version": 1, "packages": [data]})
        self.assertTrue(registry.knows_capability("hamish.sprocket"))

    def test_a_dependency_on_a_provided_capability_is_allowed(self):
        provider = {
            **metadata(),
            "id": "example.bridge",
            "name": "Bridge",
            "repository": "ExampleAuthor/Bridge",
            "kind": "loaderbridge",
            "provides": {"lavagang.melonloader": "0.7.3"},
            "install": {"payload": [{"match": "**", "target": "{Sprocket}"}], "exclude": []},
        }
        consumer = {
            **metadata(),
            "id": "example.mod",
            "dependencies": [{"id": "lavagang.melonloader", "version": ">=0.7.0", "when": "*"}],
        }
        registry = Registry.from_dict({"schema_version": 1, "packages": [provider, consumer]})
        self.assertTrue(registry.knows_capability("lavagang.melonloader"))


if __name__ == "__main__":
    unittest.main()
