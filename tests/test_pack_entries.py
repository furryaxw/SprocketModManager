"""整合包条目：成员与落点规则在 release 的清单资产里，条目可以很薄（连 `install` 都不写）。

这里钉住四件事：条目的必填与形状、清单资产要进索引、按版本合并（`when: "=该版本"`）、以及
"收窄 include/正则会削掉老版本"必须报错。
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location("sprocket_gen_index_packs", ROOT / "gen-index.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load gen-index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN_INDEX = load_module()

LIB = "test.lib"


def pack_entry(**overrides) -> dict:
    meta = {
        "schema_version": 3,
        "id": "test.pack",
        "name": "TestPack",
        "kind": "pack",
        "category": "pack",
        "repository": "test/repo",
        "release": {
            "include_prerelease": False,
            "version_pattern": r"^v?(\d+\.\d+\.\d+)$",
            "assets": {"include": ["*.zip", "*.json"], "exclude": []},
        },
    }
    meta.update(overrides)
    return meta


def mod_entry(**overrides) -> dict:
    meta = {
        "schema_version": 3,
        "id": "test.mod",
        "name": "TestMod",
        "repository": "test/repo",
        "install": {"files": []},
    }
    meta.update(overrides)
    return meta


def member(package_id: str = LIB, version: str = "=1.0.0", when: str = "*") -> dict:
    return {"id": package_id, "version": version, "when": when}


def github_asset(name: str, asset_id: int = 11, tag: str = "v1.0.0") -> dict:
    """GitHub API 的资产形状（`browser_download_url`）。"""
    return {
        "id": asset_id,
        "name": name,
        "size": 32,
        "browser_download_url": f"https://github.com/test/repo/releases/download/{tag}/{name}",
        "digest": None,
        "updated_at": "",
    }


def record_asset(name: str, asset_id: int = 11, tag: str = "v1.0.0") -> dict:
    """索引里那条 release 记录的资产形状（`download_url`）—— 清单取回看到的就是它。"""
    return {
        "id": asset_id,
        "name": name,
        "size": 32,
        "download_url": f"https://github.com/test/repo/releases/download/{tag}/{name}",
        "digest": None,
        "updated_at": "",
    }


def github_release(assets: list[dict], *, tag: str = "v1.0.0", release_id: int = 1) -> dict:
    return {
        "id": release_id,
        "tag_name": tag,
        "prerelease": False,
        "draft": False,
        "published_at": "2026-01-01T00:00:00Z",
        "html_url": f"https://github.com/test/repo/releases/tag/{tag}",
        "body": "",
        "assets": assets,
    }


def pack_block(*members: dict, asset_id: int = 11, install: dict | None = None) -> dict:
    return {
        "asset_id": asset_id,
        "asset_name": "pack.json",
        "members": [dict(item) for item in members],
        "install": install if install is not None else {},
    }


class PackEntryValidationTests(unittest.TestCase):
    def test_a_pack_entry_may_omit_install(self):
        GEN_INDEX.validate_meta(pack_entry(), "test.pack")

    def test_a_non_pack_entry_still_needs_install(self):
        entry = mod_entry()
        del entry["install"]

        with self.assertRaisesRegex(GEN_INDEX.RegistryError, "missing fields: install"):
            GEN_INDEX.validate_meta(entry, "test.mod")

    def test_members_belong_to_a_pack_entry(self):
        with self.assertRaisesRegex(GEN_INDEX.RegistryError, "members belongs to a pack entry"):
            GEN_INDEX.validate_meta(mod_entry(members=[member()]), "test.mod")

    def test_a_pack_entry_may_declare_members_as_a_baseline(self):
        GEN_INDEX.validate_meta(pack_entry(members=[member()]), "test.pack")

    def test_member_shape_is_checked(self):
        cases = {
            "missing when": ([{"id": LIB, "version": "*"}], "member is missing when"),
            "missing version": ([{"id": LIB, "when": "*"}], "member is missing version"),
            "bad version range": ([member(version="not a range")], "invalid member version"),
            "bad when range": ([member(when="not a range")], "invalid member when"),
            "bad id": ([member("NotAnId")], "invalid member id"),
            "self": ([member("test.pack")], "package cannot be its own member"),
            "duplicate": ([member(), member()], "duplicate member rule"),
            "not an object": (["test.lib"], "member entries must be objects"),
        }
        for label, (members, message) in cases.items():
            with self.subTest(case=label):
                with self.assertRaisesRegex(GEN_INDEX.RegistryError, message):
                    GEN_INDEX.validate_meta(pack_entry(members=members), "test.pack")

    def test_the_pack_category_belongs_to_packs(self):
        with self.assertRaisesRegex(GEN_INDEX.RegistryError, "category pack belongs to a pack entry"):
            GEN_INDEX.validate_meta(mod_entry(category="pack"), "test.mod")

    def test_scan_mods_refuses_an_unregistered_member(self):
        with tempfile.TemporaryDirectory() as directory:
            mods_dir = Path(directory)
            (mods_dir / "test").mkdir()
            (mods_dir / "test" / "pack.json").write_text(
                json.dumps(pack_entry(members=[member("test.ghost")])), encoding="utf-8"
            )

            with self.assertRaisesRegex(
                GEN_INDEX.RegistryError, "member is not registered: test.ghost"
            ):
                GEN_INDEX.scan_mods(mods_dir)


class PackAssetRecordingTests(unittest.TestCase):
    def test_a_pack_records_its_manifest_asset(self):
        records = GEN_INDEX.normalize_release_records(
            pack_entry(),
            [github_release([github_asset("pack.zip"), github_asset("pack.json", 12)])],
        )

        self.assertEqual([asset["name"] for asset in records[0]["assets"]], ["pack.zip", "pack.json"])

    def test_a_mod_does_not_record_json_assets(self):
        package = mod_entry()
        package["release"] = pack_entry()["release"]

        records = GEN_INDEX.normalize_release_records(
            package, [github_release([github_asset("mod.zip"), github_asset("notes.json", 12)])]
        )

        self.assertEqual([asset["name"] for asset in records[0]["assets"]], ["mod.zip"])


class PackMergeTests(unittest.TestCase):
    @staticmethod
    def package_with(*releases: dict) -> dict:
        package = pack_entry()
        package["releases"] = list(releases)
        return package

    def test_members_and_rules_are_merged_per_release(self):
        package = self.package_with(
            {
                "id": 2, "tag": "v2.0.0", "version": "2.0.0",
                "pack": pack_block(
                    member(version="=2.0.0"),
                    install={"mode": "patch", "files": [{"match": "New/**", "type": "test:new"}]},
                ),
            },
            {
                "id": 1, "tag": "v1.0.0", "version": "1.0.0",
                "pack": pack_block(
                    member(version="=1.0.0"),
                    install={"mode": "patch", "files": [{"match": "Old/**", "type": "test:old"}]},
                ),
            },
        )

        warnings = GEN_INDEX.merge_pack_manifests(package)

        self.assertEqual(warnings, [])
        self.assertEqual(
            package["members"],
            [
                {"id": LIB, "version": "=2.0.0", "when": "=2.0.0"},
                {"id": LIB, "version": "=1.0.0", "when": "=1.0.0"},
            ],
        )
        self.assertEqual(
            package["install"]["files"],
            [
                {"match": "New/**", "type": "test:new", "when": "=2.0.0"},
                {"match": "Old/**", "type": "test:old", "when": "=1.0.0"},
            ],
        )
        self.assertEqual(package["install"]["mode"], "patch")

    def test_a_rule_gated_away_from_that_release_is_not_merged(self):
        package = self.package_with({
            "id": 1, "tag": "v1.0.0", "version": "1.0.0",
            "pack": pack_block(
                member(when=">=2.0.0"),
                install={"files": [{"match": "Later/**", "type": "test:later", "when": ">=2.0.0"}]},
            ),
        })

        GEN_INDEX.merge_pack_manifests(package)

        self.assertEqual(package["members"], [])
        self.assertEqual(package["install"]["files"], [])

    def test_the_entry_baseline_stays_in_place(self):
        package = pack_entry(members=[member(version=">=1.0.0")])
        package["releases"] = [{
            "id": 1, "tag": "v1.0.0", "version": "1.0.0",
            "pack": pack_block(member(version="=1.0.0")),
        }]

        GEN_INDEX.merge_pack_manifests(package)

        self.assertEqual(package["members"][0], member(version=">=1.0.0"))
        self.assertEqual(package["members"][1], {"id": LIB, "version": "=1.0.0", "when": "=1.0.0"})

    def test_a_different_mode_in_an_older_release_warns(self):
        package = self.package_with(
            {
                "id": 2, "tag": "v2.0.0", "version": "2.0.0",
                "pack": pack_block(install={"mode": "patch"}),
            },
            {
                "id": 1, "tag": "v1.0.0", "version": "1.0.0",
                "pack": pack_block(install={"mode": "standard"}),
            },
        )

        warnings = GEN_INDEX.merge_pack_manifests(package)

        self.assertEqual(len(warnings), 1)
        self.assertIn("install.mode differs", warnings[0])

    def test_a_pack_without_any_manifest_is_left_alone(self):
        package = self.package_with({"id": 1, "tag": "v1.0.0", "version": "1.0.0"})

        self.assertEqual(GEN_INDEX.merge_pack_manifests(package), [])
        self.assertNotIn("members", package)


class PackManifestFetchTests(unittest.TestCase):
    @staticmethod
    def record(*, asset_name: str = "pack.json", asset_id: int = 12) -> dict:
        return {
            "id": 1,
            "tag": "v1.0.0",
            "version": "1.0.0",
            "assets": [record_asset(asset_name, asset_id)],
        }

    def test_a_manifest_is_downloaded_and_attached(self):
        record = self.record()
        raw = b'{"schema_version": 1, "members": [{"id": "test.lib", "version": "=1.0.0"}]}'

        with patch.object(GEN_INDEX, "_download_bytes", return_value=raw) as download:
            warnings = GEN_INDEX.fetch_pack_manifests(pack_entry(), [record])

        self.assertEqual(warnings, [])
        download.assert_called_once()
        self.assertEqual(record["pack"]["members"], [{"id": LIB, "version": "=1.0.0", "when": "*"}])
        self.assertEqual(record["pack"]["asset_id"], 12)

    def test_the_previous_index_is_reused_when_the_asset_is_unchanged(self):
        record = self.record()
        known = [{
            "id": 1,
            "tag": "v1.0.0",
            "version": "1.0.0",
            "pack": pack_block(member(), asset_id=12),
        }]

        with patch.object(
            GEN_INDEX, "_download_bytes", side_effect=AssertionError("must not download")
        ):
            warnings = GEN_INDEX.fetch_pack_manifests(pack_entry(), [record], known)

        self.assertEqual(warnings, [])
        self.assertEqual(record["pack"]["members"], [member()])

    def test_a_missing_manifest_asset_only_warns(self):
        record = self.record(asset_name="pack.zip")

        warnings = GEN_INDEX.fetch_pack_manifests(pack_entry(), [record])

        self.assertEqual(len(warnings), 1)
        self.assertIn("no manifest asset", warnings[0])
        self.assertNotIn("pack", record)

    def test_two_manifest_assets_are_an_error(self):
        record = self.record()
        record["assets"].append(record_asset("pack-extra.json", 13))

        with self.assertRaisesRegex(GEN_INDEX.RegistryError, "2 manifest assets"):
            GEN_INDEX.fetch_pack_manifests(pack_entry(), [record])

    def test_a_download_failure_only_warns(self):
        record = self.record()

        with patch.object(GEN_INDEX, "_download_bytes", side_effect=OSError("network down")):
            warnings = GEN_INDEX.fetch_pack_manifests(pack_entry(), [record])

        self.assertEqual(len(warnings), 1)
        self.assertIn("cannot download the manifest", warnings[0])
        self.assertNotIn("pack", record)

    def test_broken_manifest_content_is_an_error(self):
        record = self.record()

        with patch.object(GEN_INDEX, "_download_bytes", return_value=b"{ not json"):
            with self.assertRaises(GEN_INDEX.ScanError):
                GEN_INDEX.fetch_pack_manifests(pack_entry(), [record])

    def test_manifest_rules_are_validated(self):
        record = self.record()
        raw = b'{"schema_version": 1, "install": {"files": [{"match": "**", "type": "not a type"}]}}'

        with patch.object(GEN_INDEX, "_download_bytes", return_value=raw):
            with self.assertRaisesRegex(GEN_INDEX.RegistryError, "invalid install file type"):
                GEN_INDEX.fetch_pack_manifests(pack_entry(), [record])


class ReleaseHistoryTests(unittest.TestCase):
    def test_a_dropped_release_is_reported(self):
        package = pack_entry()
        package["releases"] = [{"tag": "v2.0.0"}]
        baseline = [{"tag": "v2.0.0"}, {"tag": "v1.0.0"}]

        messages = GEN_INDEX.validate_release_history(package, baseline)

        self.assertEqual(len(messages), 1)
        self.assertIn("v1.0.0", messages[0])
        self.assertIn("--refresh", messages[0])

    def test_nothing_dropped_reports_nothing(self):
        package = pack_entry()
        package["releases"] = [{"tag": "v2.0.0"}, {"tag": "v1.0.0"}]

        self.assertEqual(GEN_INDEX.validate_release_history(package, [{"tag": "v1.0.0"}]), [])
        self.assertEqual(GEN_INDEX.validate_release_history(package, []), [])


if __name__ == "__main__":
    unittest.main()
