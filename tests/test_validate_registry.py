"""供给表与兼容覆盖表在工作树上写坏时，报告里要出现那条问题，而不是安静地少过滤一条。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN_INDEX = load("sprocket_gen_index", "gen-index.py")
VALIDATE = load("sprocket_validate_registry", "validate_registry.py")

LOADER_ID = "lavagang.melonloader"
MODLOADERS = {LOADER_ID, "bepinex.bepinex-be", "1499501762.bepinex-melonloader-loader"}
GAME_CAPABILITY = GEN_INDEX.GAME_CAPABILITY_ID


def table_module(path: Path):
    """Stand-in for gen-index.py with PROVIDERS_FILE pointed at `path`."""

    class Module:
        PROVIDERS_FILE = path
        load_providers_table = staticmethod(GEN_INDEX.load_providers_table)

    return Module


def override_module(path: Path):
    """Stand-in for gen-index.py with OVERRIDE_FILE pointed at `path`."""

    class Module:
        OVERRIDE_FILE = path
        load_compat_overrides = staticmethod(GEN_INDEX.load_compat_overrides)
        compat_capabilities = staticmethod(GEN_INDEX.compat_capabilities)

    return Module


def game_module(path: Path):
    """Stand-in for gen-index.py with GAME_FILE pointed at `path`."""

    class Module:
        GAME_FILE = path
        load_game_file = staticmethod(GEN_INDEX.load_game_file)

    return Module


class GameFileReportTests(unittest.TestCase):
    def write(self, directory: str, payload) -> Path:
        path = Path(directory) / GEN_INDEX.GAME_FILE_NAME
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_the_shipped_file_passes(self) -> None:
        self.assertEqual(VALIDATE.validate_game(GEN_INDEX), [])

    def test_a_missing_file_is_a_failure(self) -> None:
        errors = VALIDATE.validate_game(game_module(Path("nowhere") / GEN_INDEX.GAME_FILE_NAME))

        self.assertEqual(errors, [f"{GEN_INDEX.GAME_FILE_NAME} is missing"])

    def test_a_file_without_a_usable_supply_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {"schema_version": 1, "game": {"id": GEN_INDEX.GAME_CAPABILITY_ID}})

            errors = VALIDATE.validate_game(game_module(path))

        self.assertIn(f"{GEN_INDEX.GAME_FILE_NAME} has no usable supply entry", errors)


class ProvidersTableReportTests(unittest.TestCase):
    def write(self, directory: str, payload) -> Path:
        path = Path(directory) / GEN_INDEX.PROVIDERS_FILE_NAME
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_the_shipped_table_passes(self) -> None:
        self.assertEqual(VALIDATE.validate_providers(GEN_INDEX, MODLOADERS), [])

    def test_a_missing_table_is_a_failure(self) -> None:
        errors = VALIDATE.validate_providers(table_module(Path("nowhere") / "providers.json"), MODLOADERS)

        self.assertEqual(errors, ["providers.json is missing"])

    def test_a_broken_entry_fails_with_the_warning_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory,
                {"entries": [{"loader": LOADER_ID, "version": ">=0.7.0", "sprocket": "0.2.53"}]},
            )

            errors = VALIDATE.validate_providers(table_module(path), MODLOADERS)

        self.assertEqual(len(errors), 2)
        self.assertIn("第 1 条", errors[0])
        self.assertIn("has no usable entry", errors[1])

    def test_a_table_without_a_usable_entry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {"entries": []})

            errors = VALIDATE.validate_providers(table_module(path), MODLOADERS)

        self.assertIn("providers.json has no usable entry", errors)


class CompatOverrideReportTests(unittest.TestCase):
    def write(self, directory: str, payload) -> Path:
        path = Path(directory) / GEN_INDEX.OVERRIDE_FILE_NAME
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_the_shipped_table_passes(self) -> None:
        game, _warnings = GEN_INDEX.load_game_file(GEN_INDEX.GAME_FILE)
        packages = GEN_INDEX.scan_mods(ROOT / "packages", game=game)

        self.assertEqual(VALIDATE.validate_overrides(GEN_INDEX, packages), [])

    def test_a_missing_table_is_a_failure(self) -> None:
        errors = VALIDATE.validate_overrides(
            override_module(Path("nowhere") / "override.json"), [{"id": LOADER_ID}]
        )

        self.assertEqual(errors, ["override.json is missing"])

    def test_a_broken_entry_fails_with_the_warning_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory,
                {"entries": [{"id": LOADER_ID, "compat": {GAME_CAPABILITY: "0.2.53"}}]},
            )

            errors = VALIDATE.validate_overrides(override_module(path), [{"id": LOADER_ID}])

        self.assertEqual(len(errors), 1)
        self.assertIn("第 1 条", errors[0])
        self.assertIn("少了段数", errors[0])

    def test_a_table_without_entries_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {"entries": []})

            errors = VALIDATE.validate_overrides(override_module(path), [{"id": LOADER_ID}])

        self.assertEqual(errors, [], "一条覆盖都没有不该报出来")

class LicensePlaceholderTests(unittest.TestCase):
    """`TODO-SPDX` 表示许可证还没定，两项全豁免；真实取值要求仓库带 LICENSE/COPYING 文件，GitHub 认不出该文件时以条目声明为准。"""

    class _Api:
        def __init__(self, *, license_id: str | None, paths: tuple[str, ...]) -> None:
            self.repo = {"private": False, "archived": False, "default_branch": "main"}
            if license_id is not None:
                self.repo["license"] = {"spdx_id": license_id}
            self.tree = {"truncated": False, "tree": [{"path": path, "type": "blob"} for path in paths]}
            self.releases = [{"tag_name": "v1.0.0", "assets": [{"name": "Mod.dll"}]}]

        def get(self, path: str):
            if "git/trees" in path:
                return self.tree
            if "releases" in path:
                return self.releases
            return self.repo

    @staticmethod
    def _meta(license_text: str) -> dict:
        return {
            "repository": "someone/repo",
            "license": license_text,
            "release": {
                "include_prerelease": False,
                "version_pattern": r"^v?(\d+\.\d+\.\d+)$",
                "assets": {"include": ["Mod.dll"], "exclude": []},
            },
        }

    def test_the_placeholder_waives_the_license_checks(self) -> None:
        api = self._Api(license_id=None, paths=("src/Plugin.cs",))

        errors = VALIDATE.validate_online(self._meta(VALIDATE.PLACEHOLDER_LICENSE), api)

        self.assertEqual(errors, [], "占位值下许可证两项不该报出来")

    def test_a_license_file_covers_a_repository_github_cannot_classify(self) -> None:
        api = self._Api(license_id="NOASSERTION", paths=("LICENSE", "src/Plugin.cs"))

        errors = VALIDATE.validate_online(self._meta("LGPL-3.0-only"), api)

        self.assertEqual(errors, [], "仓库带许可文件时 GitHub 认不出不该报出来")

    def test_a_real_license_value_still_needs_both_checks(self) -> None:
        api = self._Api(license_id=None, paths=("src/Plugin.cs",))

        errors = VALIDATE.validate_online(self._meta("MIT"), api)

        self.assertIn("repository must have a recognized SPDX open-source license", errors)
        self.assertIn("repository must contain a LICENSE or COPYING file", errors)


class ReleaseRuleReportTests(unittest.TestCase):
    """每个匹配 `version_pattern` 的 release 都要有匹配的资产，整合包还要读得动它的清单。

    收窄 `release.assets.include` 或改正则会让老 release 从索引里静默消失；已发布索引里还有它的
    时候就是错误，从来没进过索引的 tag 只给警告。
    """

    MEMBER = "test.lib"
    GAME_SUPPLY = {"sprocket:streamingassets": "{Sprocket}/Sprocket_Data/StreamingAssets"}

    class _Api:
        def __init__(self, releases: list[dict]) -> None:
            self.releases = releases

        def get(self, path: str):
            return self.releases

    @staticmethod
    def _meta(**overrides) -> dict:
        meta = {
            "id": "test.pack",
            "name": "TestPack",
            "kind": "pack",
            "category": "pack",
            "repository": "someone/repo",
            "release": {
                "include_prerelease": False,
                "version_pattern": r"^v?(\d+\.\d+\.\d+)$",
                "assets": {"include": ["*.zip", "*.json"], "exclude": []},
            },
        }
        meta.update(overrides)
        return meta

    @staticmethod
    def _release(tag: str, *names: str) -> dict:
        return {
            "tag_name": tag,
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": name,
                    "browser_download_url": (
                        f"https://github.com/someone/repo/releases/download/{tag}/{name}"
                    ),
                }
                for name in names
            ],
        }

    def _validate(self, meta, releases, *, published=frozenset()):
        return VALIDATE.validate_releases(
            meta,
            self._Api(releases),
            index_module=GEN_INDEX,
            packages={
                "test.pack": meta,
                self.MEMBER: {"id": self.MEMBER, "install": {"files": []}},
            },
            game_supply=self.GAME_SUPPLY,
            published_tags=frozenset(published),
        )

    def test_a_release_without_a_matching_asset_only_warns(self):
        meta = self._meta(kind="modfile", category=None)

        errors, warnings = self._validate(meta, [self._release("v1.0.0", "Mod.dll")])

        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("v1.0.0", warnings[0])

    def test_a_release_the_published_index_lists_is_an_error(self):
        meta = self._meta(kind="modfile", category=None)

        errors, _warnings = self._validate(
            meta, [self._release("v1.0.0", "Mod.dll")], published={"v1.0.0"}
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("the published index still lists it", errors[0])

    def test_a_pack_release_without_a_manifest_asset_is_an_error(self):
        errors, _warnings = self._validate(self._meta(), [self._release("v1.0.0", "pack.zip")])

        self.assertTrue(any("carries no manifest asset" in error for error in errors), errors)

    def test_two_manifest_assets_are_an_error(self):
        errors, _warnings = self._validate(
            self._meta(), [self._release("v1.0.0", "pack.zip", "pack.json", "extra.json")]
        )

        self.assertTrue(any("2 manifest assets" in error for error in errors), errors)

    def test_a_manifest_member_must_be_registered(self):
        raw = b'{"schema_version": 1, "members": [{"id": "test.ghost", "version": "*"}]}'

        with patch.object(VALIDATE, "_download_bytes", return_value=raw):
            errors, _warnings = self._validate(
                self._meta(), [self._release("v1.0.0", "pack.zip", "pack.json")]
            )

        self.assertTrue(any("member is not registered: test.ghost" in error for error in errors), errors)

    def test_a_manifest_rule_needs_a_supplier(self):
        raw = json.dumps({
            "schema_version": 1,
            "install": {"files": [{"match": "**", "type": "nobody:thing"}]},
        }).encode("utf-8")

        with patch.object(VALIDATE, "_download_bytes", return_value=raw):
            errors, _warnings = self._validate(
                self._meta(), [self._release("v1.0.0", "pack.zip", "pack.json")]
            )

        self.assertTrue(any("not supplied by any modloader" in error for error in errors), errors)

    def test_a_usable_pack_release_passes(self):
        raw = json.dumps({
            "schema_version": 1,
            "members": [{"id": self.MEMBER, "version": ">=1.0.0"}],
            "install": {
                "mode": "patch",
                "files": [{"match": "Parts/**", "type": "sprocket:streamingassets"}],
            },
        }).encode("utf-8")

        with patch.object(VALIDATE, "_download_bytes", return_value=raw):
            errors, warnings = self._validate(
                self._meta(), [self._release("v1.0.0", "pack.zip", "pack.json")]
            )

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_a_mod_never_reads_a_manifest(self):
        meta = self._meta(kind="modfile", category=None)

        with patch.object(VALIDATE, "_download_bytes", side_effect=AssertionError("must not read")):
            errors, warnings = self._validate(meta, [self._release("v1.0.0", "Mod.zip")])

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_an_unreachable_manifest_is_an_error(self):
        with patch.object(VALIDATE, "_download_bytes", side_effect=OSError("network down")):
            errors, _warnings = self._validate(
                self._meta(), [self._release("v1.0.0", "pack.zip", "pack.json")]
            )

        self.assertTrue(any("cannot download the manifest" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
