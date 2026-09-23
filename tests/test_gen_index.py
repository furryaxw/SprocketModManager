"""增量拉取 Release 的契约：先探最新版，没变就不拉列表；变了只并入新增条目。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REAL_META = ROOT / "mods" / "furryaxw.sprocket-depth" / "sprocket-mod.json"


def load_module():
    spec = importlib.util.spec_from_file_location("sprocket_gen_index", ROOT / "gen-index.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load gen-index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN_INDEX = load_module()

PACKAGE = {
    "id": "test.mod",
    "repository": "test/repo",
    "release": {
        "include_prerelease": False,
        "version_pattern": r"^v?(\d+\.\d+\.\d+)$",
        "assets": {"include": ["Mod.dll"], "exclude": []},
    },
}


def package(**overrides) -> dict:
    meta = {key: value for key, value in PACKAGE.items()}
    meta.update(overrides)
    return meta


def record(release_id: int, version: str, *, prerelease: bool = False) -> dict:
    return {
        "id": release_id,
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": prerelease,
        "html_url": f"https://github.com/test/repo/releases/tag/v{version}",
        "published_at": f"2026-09-{release_id:02d}T00:00:00Z",
        "assets": [
            {
                "id": release_id * 10,
                "name": "Mod.dll",
                "size": 1024,
                "browser_download_url": (
                    f"https://github.com/test/repo/releases/download/v{version}/Mod.dll"
                ),
                "digest": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ],
    }


def normalize(meta: dict, *releases: dict) -> list[dict]:
    return GEN_INDEX.normalize_release_records(meta, list(releases))


class MergeReleaseTests(unittest.TestCase):
    def test_new_release_is_added_and_older_known_ones_survive(self) -> None:
        meta = package()
        known = normalize(meta, record(1, "1.0.0"), record(2, "1.1.0"))
        fetched = normalize(meta, record(3, "1.2.0"), record(2, "1.1.0"))

        merged = GEN_INDEX.merge_releases(known, fetched)

        self.assertEqual([entry["version"] for entry in merged], ["1.2.0", "1.1.0", "1.0.0"],
                         "拉到的最老是 1.1.0，比它更老的 1.0.0 沿用手上那份")

    def test_a_release_removed_upstream_disappears(self) -> None:
        meta = package()
        known = normalize(meta, record(1, "1.0.0"), record(2, "1.1.0"), record(3, "1.2.0"))
        fetched = normalize(meta, record(2, "1.1.0"), record(1, "1.0.0"))

        merged = GEN_INDEX.merge_releases(known, fetched)

        self.assertEqual([entry["version"] for entry in merged], ["1.1.0", "1.0.0"],
                         "拉取窗口内的版本以新数据为准，被删掉的 1.2.0 不该留下")

    def test_nothing_fetched_keeps_the_known_list(self) -> None:
        meta = package()
        known = normalize(meta, record(1, "1.0.0"))
        self.assertEqual(GEN_INDEX.merge_releases(known, []), known)


class IncrementalFetchTests(unittest.TestCase):
    def test_unchanged_latest_reuses_known_without_fetching_the_list(self) -> None:
        meta = package()
        known = normalize(meta, record(1, "1.0.0"), record(2, "1.1.0"))
        calls: list[str] = []

        def fake_json(path: str, *, missing_ok: bool = False):
            calls.append(path)
            if path.endswith("/releases/latest"):
                return record(2, "1.1.0")
            raise AssertionError(f"不该请求：{path}")

        with patch.object(GEN_INDEX, "_github_json", fake_json):
            result = GEN_INDEX.fetch_package_releases(meta, known)

        self.assertEqual(result, known)
        self.assertEqual(calls, ["/repos/test/repo/releases/latest"],
                         "最新版没变就只发一个探针请求")

    def test_changed_latest_fetches_the_list_and_appends_only_the_new_release(self) -> None:
        meta = package()
        known = normalize(meta, record(1, "1.0.0"), record(2, "1.1.0"))
        calls: list[str] = []

        def fake_json(path: str, *, missing_ok: bool = False):
            calls.append(path)
            if path.endswith("/releases/latest"):
                return record(3, "1.2.0")
            if "?per_page=100" in path:
                return [record(3, "1.2.0"), record(2, "1.1.0")]
            raise AssertionError(f"不该请求：{path}")

        with patch.object(GEN_INDEX, "_github_json", fake_json):
            result = GEN_INDEX.fetch_package_releases(meta, known)

        self.assertEqual([entry["version"] for entry in result], ["1.2.0", "1.1.0", "1.0.0"])
        self.assertEqual(
            calls,
            ["/repos/test/repo/releases/latest", "/repos/test/repo/releases?per_page=100"],
        )

    def test_prerelease_packages_skip_the_probe(self) -> None:
        meta = package(
            release={
                "include_prerelease": True,
                "version_pattern": r"^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$",
                "assets": {"include": ["Mod.dll"], "exclude": []},
            }
        )
        known = normalize(meta, record(1, "1.0.0"))
        calls: list[str] = []

        def fake_json(path: str, *, missing_ok: bool = False):
            calls.append(path)
            if "?per_page=100" in path:
                return [record(2, "1.1.0-rc1"), record(1, "1.0.0")]
            raise AssertionError(f"不该请求：{path}")

        with patch.object(GEN_INDEX, "_github_json", fake_json):
            result = GEN_INDEX.fetch_package_releases(meta, known)

        self.assertEqual([entry["version"] for entry in result], ["1.1.0-rc1", "1.0.0"])
        self.assertEqual(calls, ["/repos/test/repo/releases?per_page=100"],
                         "/releases/latest 永远不返回预发布，所以这类包直接拉列表")

    def test_first_run_without_a_baseline_fetches_the_list(self) -> None:
        meta = package()
        calls: list[str] = []

        def fake_json(path: str, *, missing_ok: bool = False):
            calls.append(path)
            if "?per_page=100" in path:
                return [record(1, "1.0.0")]
            raise AssertionError(f"不该请求：{path}")

        with patch.object(GEN_INDEX, "_github_json", fake_json):
            result = GEN_INDEX.fetch_package_releases(meta)

        self.assertEqual([entry["version"] for entry in result], ["1.0.0"])
        self.assertEqual(calls, ["/repos/test/repo/releases?per_page=100"])


class BaselineTests(unittest.TestCase):
    def test_a_written_index_reads_back_as_the_baseline(self) -> None:
        meta = package()
        with tempfile.TemporaryDirectory() as directory:
            index_path = Path(directory) / "index.json"
            index_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "game": "sprocket",
                        "generated_at": "2026-09-22T00:00:00Z",
                        "packages": [{"id": "test.mod", **{k: v for k, v in meta.items() if k != "id"},
                                      "releases": normalize(meta, record(1, "1.0.0"))}],
                    }
                ),
                encoding="utf-8",
            )

            baseline = GEN_INDEX.load_index_releases(index_path)

        self.assertEqual([entry["version"] for entry in baseline["test.mod"]], ["1.0.0"])

    def test_the_loader_gets_the_baseline_and_a_failed_fetch_keeps_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mods_dir = Path(directory) / "mods"
            (mods_dir / "furryaxw.sprocket-depth").mkdir(parents=True)
            (mods_dir / "furryaxw.sprocket-depth" / "sprocket-mod.json").write_bytes(
                REAL_META.read_bytes()
            )
            meta = json.loads(REAL_META.read_text(encoding="utf-8"))
            known = normalize(
                meta,
                {
                    "id": 1,
                    "tag_name": "v0.1.2",
                    "draft": False,
                    "prerelease": False,
                    "html_url": "https://github.com/furryaxw/SprocketDepth/releases/tag/v0.1.2",
                    "published_at": "2026-09-22T00:00:00Z",
                    "assets": [
                        {
                            "id": 11,
                            "name": "SprocketDepth.dll",
                            "size": 17920,
                            "browser_download_url": (
                                "https://github.com/furryaxw/SprocketDepth/releases/download/"
                                "v0.1.2/SprocketDepth.dll"
                            ),
                            "digest": None,
                            "updated_at": "2026-09-22T00:00:00Z",
                        }
                    ],
                },
            )
            seen: dict[str, list[dict]] = {}

            def loader(package_meta: dict, previous: list[dict]) -> list[dict]:
                seen[package_meta["id"]] = list(previous)
                raise RuntimeError("network down")

            output = Path(directory) / "index.json"
            index = GEN_INDEX.generate_index(
                mods_dir,
                output,
                release_loader=loader,
                baseline_releases={"furryaxw.sprocket-depth": known},
            )
            written = output.is_file()

        self.assertEqual(seen["furryaxw.sprocket-depth"], known, "拉取前先把已知的交给 loader")
        self.assertEqual(index["packages"][0]["releases"], known, "拉取失败就沿用基线那份")
        self.assertTrue(written, "索引仍然要落盘")


class CompatibilityBlockTests(unittest.TestCase):
    def block(self, payload: str) -> str:
        return f"## 依赖\n\n- Sprocket `0.2.53.2`\n\n<!-- sp-compat\n{payload}\n-->\n"

    def test_a_release_without_a_block_declares_nothing(self) -> None:
        self.assertEqual(GEN_INDEX.parse_compat_block("## 更新\n\n- 修了个 bug"), ({}, []))

    def test_adjacent_versions_collapse_into_one_range(self) -> None:
        declared, warnings = GEN_INDEX.parse_compat_block(
            self.block('{"sprocket": ["0.2.53.1", "0.2.53.2"]}')
        )
        self.assertEqual(declared, {"sprocket": ">=0.2.53.1 <=0.2.53.2"})
        self.assertEqual(warnings, [])

    def test_wildcards_become_the_next_segment_bound(self) -> None:
        declared, _ = GEN_INDEX.parse_compat_block(
            self.block('{"sprocket": ["0.2.53.x"], "melonloader": ["0.7.x"]}')
        )
        self.assertEqual(declared["sprocket"], ">=0.2.53.0 <0.2.54.0")
        self.assertEqual(declared["melonloader"], ">=0.7.0 <0.8.0")

    def test_ranges_and_disjoint_unions_survive(self) -> None:
        declared, _ = GEN_INDEX.parse_compat_block(
            self.block('{"sprocket": [">=0.2.50 <0.2.54"], "melonloader": ["0.7.x", "0.9.x"]}')
        )
        self.assertEqual(declared["sprocket"], ">=0.2.50.0 <0.2.54.0")
        self.assertEqual(declared["melonloader"], ">=0.7.0 <0.8.0 || >=0.9.0 <0.10.0")

    def test_unknown_axes_are_a_warning_not_an_error(self) -> None:
        declared, warnings = GEN_INDEX.parse_compat_block(
            self.block('{"sprocket": ["0.2.53.x"], "unity": ["2022.3"]}')
        )
        self.assertEqual(declared, {"sprocket": ">=0.2.53.0 <0.2.54.0"})
        self.assertEqual(len(warnings), 1)
        self.assertIn("unity", warnings[0])

    def test_broken_blocks_are_rejected(self) -> None:
        for payload in (
            "{not json}",
            '["0.2.53.1"]',                       # 不是对象
            '{"sprocket": []}',                   # 空列表
            '{"sprocket": ["0.2.53"]}',           # 游戏是 4 段，少一段
            '{"sprocket": ["0.2.x.1"]}',          # 通配只能放最后
            '{"sprocket": [">=0.2.54 <0.2.50"]}',  # 上界低于下界
            '{"melonloader": [1]}',               # 不是字符串
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(GEN_INDEX.CompatibilityError):
                    GEN_INDEX.parse_compat_block(self.block(payload))


class ReleaseCompatibilityTests(unittest.TestCase):
    def release(self, tag: str, version: str, body: str = "", **extra) -> dict:
        return {"id": int(version.replace(".", "")), "tag": tag, "version": version,
                "prerelease": False, "published_at": "", "page_url": "", "assets": [],
                "compat_declared": GEN_INDEX.parse_compat_block(body)[0],
                "compat_warnings": GEN_INDEX.parse_compat_block(body)[1],
                "compat_invalid": False, **extra}

    def test_declared_releases_depend_on_the_environment_packages(self) -> None:
        body = '<!-- sp-compat {"sprocket": ["0.2.53.1", "0.2.53.2"], "melonloader": [">=0.7.2 <0.8"]} -->'
        releases = GEN_INDEX.apply_release_compatibility([self.release("v2.5.2", "2.5.2", body)])
        entry = releases[0]
        self.assertEqual(
            entry["dependencies"],
            [
                {"id": "environment.sprocket", "version": ">=0.2.53.1 <=0.2.53.2"},
                {"id": "environment.melonloader", "version": ">=0.7.2 <0.8.0"},
            ],
        )
        self.assertEqual(entry["compatibility"], {"source": "declared"})
        self.assertNotIn("compat_declared", entry, "内部字段不该留在索引里")

    def test_a_release_without_a_block_inherits_the_nearest_older_declaration(self) -> None:
        body = '<!-- sp-compat {"sprocket": ["0.2.53.1"]} -->'
        releases = GEN_INDEX.apply_release_compatibility([
            self.release("v2.4.4", "2.4.4", body),
            self.release("v2.5.0", "2.5.0"),
            self.release("v2.5.1", "2.5.1"),
        ])
        by_tag = {entry["tag"]: entry for entry in releases}
        self.assertEqual(by_tag["v2.5.0"]["compatibility"], {"source": "inherited", "from_tag": "v2.4.4"})
        self.assertEqual(by_tag["v2.5.1"]["compatibility"], {"source": "inherited", "from_tag": "v2.4.4"},
                         "继承链始终指向最初声明的那个 tag")
        self.assertEqual(by_tag["v2.5.1"]["dependencies"], by_tag["v2.4.4"]["dependencies"])

    def test_releases_older_than_any_declaration_stay_unknown(self) -> None:
        body = '<!-- sp-compat {"sprocket": ["0.2.53.1"]} -->'
        releases = GEN_INDEX.apply_release_compatibility([
            self.release("v1.0.0", "1.0.0"),
            self.release("v2.0.0", "2.0.0", body),
        ])
        by_tag = {entry["tag"]: entry for entry in releases}
        self.assertNotIn("dependencies", by_tag["v1.0.0"])
        self.assertNotIn("compatibility", by_tag["v1.0.0"])

    def test_a_broken_block_is_reported_and_does_not_break_the_chain(self) -> None:
        good = '<!-- sp-compat {"sprocket": ["0.2.53.1"]} -->'
        broken = self.release("v2.5.0", "2.5.0")
        broken["compat_invalid"] = True
        broken["compat_warnings"] = ["sp-compat 块不是合法 JSON"]
        releases = GEN_INDEX.apply_release_compatibility([
            self.release("v2.4.4", "2.4.4", good),
            broken,
            self.release("v2.5.1", "2.5.1"),
        ])
        by_tag = {entry["tag"]: entry for entry in releases}
        self.assertNotIn("dependencies", by_tag["v2.5.0"], "写坏的声明不继承")
        self.assertEqual(by_tag["v2.5.0"]["compatibility"]["warnings"], ["sp-compat 块不是合法 JSON"])
        self.assertEqual(by_tag["v2.5.1"]["compatibility"]["from_tag"], "v2.4.4",
                         "链条继续从最近一个可用声明接上")

    def test_an_already_resolved_baseline_entry_still_feeds_the_chain(self) -> None:
        releases = GEN_INDEX.apply_release_compatibility([
            {
                "id": 1, "tag": "v2.5.2", "version": "2.5.2", "prerelease": False,
                "published_at": "", "page_url": "", "assets": [],
                "dependencies": [{"id": "environment.sprocket", "version": ">=0.2.53.1 <=0.2.53.2"}],
                "compatibility": {"source": "inherited", "from_tag": "v2.4.4"},
            },
            {"id": 2, "tag": "v2.5.3", "version": "2.5.3", "prerelease": False,
             "published_at": "", "page_url": "", "assets": [],
             "compat_declared": {}, "compat_warnings": [], "compat_invalid": False},
        ])
        by_tag = {entry["tag"]: entry for entry in releases}
        self.assertEqual(by_tag["v2.5.3"]["compatibility"], {"source": "inherited", "from_tag": "v2.4.4"},
                         "沿用基线里那份，并且继续指向最初的声明")


class CompatibleIndexTests(unittest.TestCase):
    def test_fetch_carries_the_block_into_the_index(self) -> None:
        meta = package()
        body = '<!-- sp-compat {"sprocket": ["0.2.53.x"]} -->'
        record = globals()["record"](1, "1.0.0")
        record["body"] = body

        def fake_json(path: str, *, missing_ok: bool = False):
            if "?per_page=100" in path:
                return [record]
            raise AssertionError(path)

        with patch.object(GEN_INDEX, "_github_json", fake_json):
            releases = GEN_INDEX.fetch_package_releases(meta)

        self.assertEqual(
            releases[0]["dependencies"],
            [{"id": "environment.sprocket", "version": ">=0.2.53.0 <0.2.54.0"}],
        )
        self.assertEqual(releases[0]["compatibility"], {"source": "declared"})

    def test_a_broken_block_does_not_break_the_fetch(self) -> None:
        meta = package()
        record = globals()["record"](1, "1.0.0")
        record["body"] = '<!-- sp-compat {"sprocket": ["0.2.53"]} -->'

        def fake_json(path: str, *, missing_ok: bool = False):
            if "?per_page=100" in path:
                return [record]
            raise AssertionError(path)

        with patch.object(GEN_INDEX, "_github_json", fake_json):
            releases = GEN_INDEX.fetch_package_releases(meta)

        self.assertEqual([entry["version"] for entry in releases], ["1.0.0"])
        self.assertNotIn("dependencies", releases[0])
        self.assertEqual(len(releases[0]["compatibility"]["warnings"]), 1)

    def test_generated_index_declares_the_virtual_packages_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mods_dir = Path(directory) / "mods"
            (mods_dir / "furryaxw.sprocket-depth").mkdir(parents=True)
            (mods_dir / "furryaxw.sprocket-depth" / "sprocket-mod.json").write_bytes(
                REAL_META.read_bytes()
            )
            recorded = {
                "id": 1, "tag": "v0.1.2", "version": "0.1.2", "prerelease": False,
                "published_at": "", "page_url": "", "assets": [],
                "compatibility": {"source": "declared", "warnings": ["sprocket 版本写错了一段"]},
            }

            index = GEN_INDEX.generate_index(
                mods_dir,
                Path(directory) / "index.json",
                release_loader=lambda _package, _known: [dict(recorded)],
            )

        self.assertEqual(index["virtual_packages"], ["environment.sprocket", "environment.melonloader"])
        warnings = index["packages"][0]["compatibility_warnings"]
        self.assertEqual(warnings, ["v0.1.2: sprocket 版本写错了一段"])


class EnvironmentTableTests(unittest.TestCase):
    """`site/environment.json`：加载器能跑哪段游戏版本。"""

    def write_table(self, directory: str, payload) -> Path:
        path = Path(directory) / GEN_INDEX.ENVIRONMENT_FILE_NAME
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        path.write_text(text, encoding="utf-8")
        return path

    def test_entries_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_table(
                directory,
                {
                    "schema_version": 1,
                    "entries": [{"melonloader": ">=0.7.0 <0.8", "sprocket": "<0.2.54"}],
                },
            )
            table, warnings = GEN_INDEX.load_environment_table(path)

        self.assertEqual(warnings, [])
        self.assertEqual(
            table,
            {
                "schema_version": 1,
                "entries": [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}],
            },
        )

    def test_a_missing_file_is_an_empty_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            table, warnings = GEN_INDEX.load_environment_table(Path(directory) / "nope.json")

        self.assertEqual(table, {"schema_version": 1, "entries": []})
        self.assertEqual(warnings, [])

    def test_a_broken_entry_is_reported_and_the_rest_survive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_table(
                directory,
                {
                    "entries": [
                        {"melonloader": ">=0.7.0 <0.6.0", "sprocket": "<0.2.54"},
                        {"melonloader": ">=0.6.0 <0.7.0", "sprocket": "0.2.53"},
                        {"melonloader": ">=0.5.0"},
                        "nope",
                        {"melonloader": ">=0.7.0 <0.8", "sprocket": "<0.2.54"},
                    ]
                },
            )
            table, warnings = GEN_INDEX.load_environment_table(path)

        self.assertEqual(len(warnings), 4)
        self.assertIn("第 1 条", warnings[0], "上界低于下界")
        self.assertIn("第 2 条", warnings[1], "4 段的游戏版本要写全，不能只写 0.2.53")
        self.assertIn("第 3 条", warnings[2], "缺了 sprocket")
        self.assertIn("第 4 条", warnings[3], "必须是对象")
        self.assertEqual(
            table["entries"],
            [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}],
        )

    def test_an_unreadable_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_table(directory, "{ not json")
            table, warnings = GEN_INDEX.load_environment_table(path)

        self.assertEqual(table, {"schema_version": 1, "entries": []})
        self.assertEqual(len(warnings), 1)

    def test_the_generated_index_carries_the_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mods_dir = Path(directory) / "mods"
            (mods_dir / "furryaxw.sprocket-depth").mkdir(parents=True)
            (mods_dir / "furryaxw.sprocket-depth" / "sprocket-mod.json").write_bytes(
                REAL_META.read_bytes()
            )
            environment_file = self.write_table(
                directory,
                {"entries": [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54"}]},
            )

            index = GEN_INDEX.generate_index(
                mods_dir,
                Path(directory) / "index.json",
                environment_file=environment_file,
            )

        self.assertEqual(
            index["environment"]["entries"],
            [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}],
        )
        self.assertEqual(index["environment_warnings"], [])

    def test_the_shipped_table_uses_the_known_loader_and_game_axes(self) -> None:
        table, warnings = GEN_INDEX.load_environment_table(GEN_INDEX.ENVIRONMENT_FILE)

        self.assertEqual(warnings, [])
        self.assertGreaterEqual(len(table["entries"]), 1)
        for entry in table["entries"]:
            self.assertEqual(set(entry), {"melonloader", "sprocket"})


if __name__ == "__main__":
    unittest.main()
