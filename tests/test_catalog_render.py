"""在 Node 里真实执行目录页与环境区的渲染逻辑。

覆盖三件契约：兼容性隐藏 + 「显示不兼容」开关、版本号颜色、左下角（未安装=链接、环境矛盾=标红 + 原因）。
没有 node 时自动跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "fixtures" / "client_ui" / "render_catalog_harness.js"
CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
NODE = shutil.which("node")


def package(
        package_id: str,
        releases: list[tuple[str, str]],
        *,
        inherited_from: str = "",
        category: str = "utility",
        sprocket_range: str = "",
        melonloader_range: str = "",
) -> dict:
    latest_version, latest_verdict = releases[0]
    return {
        "id": package_id,
        "name": package_id,
        "display_name": {"en": package_id, "zh": package_id},
        "description": {"en": "", "zh": ""},
        "authors": ["someone"],
        "repository": "test/repo",
        "repository_url": "https://github.com/test/repo",
        "license": "MIT",
        "category": category,
        "tags": [],
        "dependencies": [],
        "recommendations": [],
        "featured": False,
        "install_assets": ["Mod.dll"],
        "installed": None,
        "release": {"tag": f"v{latest_version}", "version": latest_version, "verdict": latest_verdict, "assets": []},
        "releases": [
            {
                "tag": f"v{version}",
                "version": version,
                "verdict": verdict,
                "compatibility": (
                    {"source": "inherited", "from_tag": inherited_from}
                    if inherited_from and index == 0
                    else {"source": "declared"}
                ),
                # 逐轴结果与两组声明区间只挂在最新那版上（详情页要用）。
                "dependencies": [
                    *(
                        [{"id": "environment.sprocket", "version": sprocket_range}]
                        if index == 0 and sprocket_range
                        else []
                    ),
                    *(
                        [{"id": "environment.melonloader", "version": melonloader_range}]
                        if index == 0 and melonloader_range
                        else []
                    ),
                ],
                "axes": (
                    [
                        {
                            "id": "environment.sprocket",
                            "declared": sprocket_range,
                            "local": "0.2.53.2",
                            "satisfied": None if not sprocket_range else latest_verdict == "compatible",
                        },
                        {
                            "id": "environment.melonloader",
                            "declared": melonloader_range,
                            "local": "0.7.3",
                            "satisfied": None if not melonloader_range else latest_verdict == "compatible",
                        },
                    ]
                    if index == 0
                    else []
                ),
            }
            for index, (version, verdict) in enumerate(releases)
        ],
    }


def environment(
        *,
        installed: bool = True,
        sprocket: str = "0.2.53.2",
        state: str = "ok",
        conflict: bool = False,
) -> dict:
    return {
        "sprocket": {"state": state, "version": sprocket, "raw": sprocket, "detail": ""},
        "melonloader": {
            "installed": installed,
            "version": "0.7.3" if installed else None,
            "latest_version": "0.7.3",
            "used_version": "0.7.3",
        },
        "environment": {"state": "conflict" if conflict else "ok", "entry": None, "table_source": "registry"},
        "revision": 1,
    }


def _walk(node) -> list[dict]:
    """把序列化后的 DOM 摊平，便于断言某个类名出现在哪一层。"""
    if not node:
        return []
    found = [node]
    for child in node.get("children") or []:
        found.extend(_walk(child))
    return found


class CatalogRenderHarnessTests(unittest.TestCase):
    def _render(self, **payload) -> dict:
        payload.setdefault("packages", [])
        payload.setdefault("environment", environment())
        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(HARNESS), str(CLIENT_UI), str(payload_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, (completed.stdout or "") + (completed.stderr or ""))
            return json.loads(completed.stdout or "{}")

    @staticmethod
    def _texts(node) -> list[str]:
        found = [node["text"]] if node.get("text") else []
        for child in node.get("children") or []:
            found.extend(CatalogRenderHarnessTests._texts(child))
        return found

    def test_a_package_with_only_incompatible_releases_is_hidden(self) -> None:
        payload = {
            "packages": [
                package("test.old", [("1.0.0", "incompatible")]),
                package("test.fine", [("2.0.0", "compatible")]),
            ],
        }

        result = self._render(**payload)

        self.assertEqual(result["diagnostics"]["known"], 2)
        self.assertEqual(result["diagnostics"]["hidden"], ["test.old"])
        self.assertEqual(result["count"], "1", "只剩兼容的那个")
        self.assertEqual(len(result["rows"]), 1)
        ids = " ".join(text for row in result["rows"] for text in self._texts(row))
        self.assertIn("test.fine", ids)
        self.assertNotIn("test.old", ids)
        self.assertFalse(result["notice"]["hidden"], "隐藏了东西就要说明")
        self.assertIn("1 mods hidden as incompatible", " ".join(self._texts(result["notice"])))
        self.assertEqual(result["toggle"]["text"], "Show incompatible")

    def test_the_toggle_brings_the_hidden_package_back(self) -> None:
        result = self._render(
            packages=[package("test.old", [("1.0.0", "incompatible")])],
            hidden_count=1,
        )

        self.assertEqual(result["afterToggle"]["showIncompatible"], True)
        self.assertEqual(result["afterToggle"]["rows"], 1)

    def test_the_toggle_off_hides_it_again(self) -> None:
        result = self._render(
            packages=[package("test.old", [("1.0.0", "incompatible")])],
            hidden_count=1,
            show_incompatible=True,
        )

        self.assertEqual(result["toggle"]["text"], "Hide incompatible")
        self.assertEqual(result["afterToggle"]["showIncompatible"], False)
        self.assertEqual(result["afterToggle"]["rows"], 0)

    def test_a_partly_compatible_package_stays_visible(self) -> None:
        result = self._render(
            packages=[package("test.partly", [("2.0.0", "incompatible"), ("1.0.0", "compatible")])],
        )

        self.assertEqual(len(result["rows"]), 1)
        self.assertTrue(result["notice"]["hidden"], "没有隐藏就不出说明")

    def test_a_translation_package_is_shown_without_a_verdict_mark(self) -> None:
        """翻译包不参与环境判定：不隐藏、不标色、不挂 chip（它的依赖各自在目录里有颜色）。"""
        result = self._render(
            page="translations",
            packages=[package("test.translation", [("1.0.0", "not_applicable")], category="translation")],
            selected="test.translation",
        )

        self.assertEqual(result["count"], "1", "不参与判定的包不进「全不兼容」的隐藏里")
        self.assertEqual(len(result["rows"]), 1)
        version = next(
            child for child in result["rows"][0]["children"] if child["className"].startswith("package-version")
        )
        number = version["children"][0]
        self.assertEqual(number["className"], "", "版本号不带三色类")
        chips = [
            node for node in _walk(result["detail"]) if node["className"].startswith("state-chip")
        ]
        self.assertFalse(
            [chip for chip in chips if "compatible" in chip["className"] or "incompatible" in chip["className"]],
            f"详情页不该有判定 chip：{chips}",
        )

    def test_a_translation_package_with_a_red_dependency_still_shows_itself(self) -> None:
        # 依赖在目录里照常标红；翻译包本身仍然可见（只是不判自己）。
        result = self._render(
            page="translations",
            packages=[
                package("test.translation", [("1.0.0", "not_applicable")], category="translation"),
                package("test.dep", [("9.0.0", "incompatible")]),
            ],
        )

        visible = " ".join(text for row in result["rows"] for text in self._texts(row))
        self.assertIn("test.translation", visible)
        self.assertNotIn("test.dep", visible, "非翻译包不进翻译列表")

    def test_the_detail_page_shows_the_concrete_compatibility_facts(self) -> None:
        result = self._render(
            packages=[package(
                "test.fine",
                [("2.0.0", "compatible")],
                sprocket_range=">=0.2.53.0 <0.2.54.0",
                melonloader_range=">=0.7.3 <=0.7.3",
            )],
            selected="test.fine",
        )

        sections = [child["className"] for child in result["detail"]["children"]]
        self.assertEqual(sections[-1], "compatibility-section", "挂在依赖/推荐那一排下面")
        self.assertIn("detail-sections", sections, "依赖与推荐并排")
        section = result["detail"]["children"][-1]
        text = " ".join(node["text"] or "" for node in _walk(section))
        self.assertIn("COMPATIBILITY", text)
        self.assertIn("Sprocket", text)
        self.assertIn(">=0.2.53.0 <0.2.54.0", text, "声明原文要摆出来")
        self.assertIn("this machine: 0.2.53.2", text, "本机值也要摆出来")
        self.assertIn("MelonLoader", text)
        self.assertIn("this machine: 0.7.3", text)
        self.assertNotIn("Compatible", text, "兼容是默认状态，不再单独写一行")

    def test_an_incompatible_release_says_so_in_the_facts(self) -> None:
        result = self._render(
            packages=[package(
                "test.red", [("2.0.0", "incompatible")], sprocket_range=">=0.2.54.0"
            )],
            selected="test.red",
            show_incompatible=True,
        )

        section = result["detail"]["children"][-1]
        text = " ".join(node["text"] or "" for node in _walk(section))
        tones = [node["className"] for node in _walk(section) if node["className"]]
        self.assertIn("Incompatible", text, "不兼容才把判定摆出来")
        self.assertIn("fail", tones)

    def test_the_recommendation_line_does_not_repeat_the_package_id(self) -> None:
        target = package("test.unknown", [("1.0.0", "unknown")])
        source = package("test.source", [("1.0.0", "compatible")])
        source["recommendations"] = ["test.unknown", "test.absent"]
        result = self._render(packages=[source, target], selected="test.source")

        lines = [
            node for node in _walk(result["detail"]) if node["className"] == "dependency-line"
        ]
        texts = [" ".join(child["text"] or "" for child in line["children"]) for line in lines]
        self.assertIn("test.unknown", texts, "认得出来的包只写名称")
        self.assertIn("test.absent", texts, "认不出来的包写 id")
        for line in lines:
            for name in ("test.unknown", "test.absent"):
                if name not in texts[lines.index(line)]:
                    continue
                # 同一个 id 在一行里只出现一次。
                self.assertEqual(texts[lines.index(line)].count(name), 1)

    def test_an_inherited_version_carries_a_star(self) -> None:
        result = self._render(
            packages=[package(
                "test.inherited",
                [("2.0.0", "compatible")],
                inherited_from="v1.0.0",
                sprocket_range="0.2.53.x",
            )],
            selected="test.inherited",
        )

        heading = next(
            child for child in result["detail"]["children"] if child["className"] == "detail-heading"
        )
        group = next(
            child for child in heading["children"] if child["className"] == "detail-version-group"
        )
        self.assertEqual(group["children"][-1]["text"], "2.0.0*", "沿用更早声明就缀一个星号")
        section = result["detail"]["children"][-1]
        text = " ".join(node["text"] or "" for node in _walk(section))
        self.assertIn("reuses the older compatibility declaration", text)
        self.assertNotIn("v1.0.0", text, "星号只说「沿用更早那份」，不再点名继承自哪个 tag")

    def test_a_declared_version_has_no_star(self) -> None:
        result = self._render(
            packages=[package("test.fine", [("2.0.0", "compatible")], sprocket_range="0.2.53.x")],
            selected="test.fine",
        )

        heading = next(
            child for child in result["detail"]["children"] if child["className"] == "detail-heading"
        )
        group = next(
            child for child in heading["children"] if child["className"] == "detail-version-group"
        )
        self.assertEqual(group["children"][-1]["text"], "2.0.0")

    def test_the_selection_bar_stays_hidden_until_something_is_picked(self) -> None:
        result = self._render(packages=[package("test.fine", [("1.0.0", "compatible")])])

        state = result["selectionAfterRows"]
        self.assertTrue(state["hidden"], "没勾选就不占地方")
        self.assertEqual(state["count"], "")
        self.assertEqual(
            state["buttons"],
            {"install": True, "remove": True, "all": False, "invert": False, "clear": True},
            "没选中时只有全选/反选可用（它们不依赖已有选择）",
        )

    def test_right_clicking_a_row_picks_it_for_batch_actions(self) -> None:
        result = self._render(
            packages=[
                package("test.fine", [("1.0.0", "compatible")]),
                package("test.other", [("1.0.0", "compatible")]),
            ],
            select_rows=[1],
        )

        state = result["selectionAfterRows"]
        self.assertFalse(state["hidden"])
        self.assertEqual(state["count"], "1 selected")
        self.assertFalse(state["buttons"]["install"], "选中了可安装的项 → 安装可用")
        self.assertTrue(state["buttons"]["remove"], "没装过 → 卸载仍然不可用")
        self.assertFalse(state["buttons"]["clear"])

    def test_installed_rows_enable_the_uninstall_action(self) -> None:
        installed = package("test.fine", [("1.0.0", "compatible")])
        installed["installed"] = {"name": "test.fine", "version": "1.0.0", "requested": True,
                                 "corrupted": False, "integrity": "release", "suppressed": False, "files": []}
        result = self._render(packages=[installed], select_rows=[0])

        state = result["selectionAfterRows"]
        self.assertFalse(state["buttons"]["remove"], "有安装记录 → 卸载可用")

    def test_a_row_with_nothing_to_install_can_still_be_picked(self) -> None:
        """装到最新只是「安装」没活干，勾选还要给「卸载」用，所以选择框不能灰掉。"""
        current = package("test.current", [("1.0.0", "compatible")])
        current["installed"] = {"name": "test.current", "version": "1.0.0", "requested": True,
                                "corrupted": False, "integrity": "release", "suppressed": False, "files": []}
        result = self._render(packages=[current], check_row=0)

        checkbox = result["rows"][0]["children"][0]
        self.assertEqual(checkbox["className"], "package-check")
        self.assertFalse(checkbox["disabled"], "选择框只表示被选中，能不能装由浮动栏判断")
        state = result["selectionAfterCheck"]
        self.assertEqual(state["count"], "1 selected")
        self.assertFalse(state["buttons"]["remove"], "选中的这一行装了 → 卸载可用")
        self.assertTrue(state["buttons"]["install"], "它没有可装的版本 → 安装按钮自己灰")

    def test_select_all_and_clear_drive_the_same_selection(self) -> None:
        packages = [
            package("test.a", [("1.0.0", "compatible")]),
            package("test.b", [("1.0.0", "compatible")]),
            package("test.c", [("1.0.0", "compatible")]),
        ]
        everything = self._render(packages=packages, selection_action="all")["selectionAfterAction"]
        self.assertEqual(everything["count"], "3 selected")

        cleared = self._render(packages=packages, select_rows=[0], selection_action="clear")
        self.assertTrue(cleared["selectionAfterAction"]["hidden"])
        self.assertEqual(cleared["selectionAfterAction"]["count"], "")

    def test_invert_flips_the_visible_rows(self) -> None:
        packages = [
            package("test.a", [("1.0.0", "compatible")]),
            package("test.b", [("1.0.0", "compatible")]),
            package("test.c", [("1.0.0", "compatible")]),
        ]
        result = self._render(packages=packages, select_rows=[0], selection_action="invert")

        self.assertEqual(result["selectionAfterAction"]["count"], "2 selected")

    def test_the_latest_version_number_is_coloured_by_its_verdict(self) -> None:
        result = self._render(
            packages=[
                package("test.unknown", [("1.0.0", "unknown")]),
                package("test.red", [("1.0.0", "incompatible")]),
                package("test.white", [("1.0.0", "compatible")]),
            ],
            show_incompatible=True,
        )

        versions = {
            self._texts(row)[0]: next(
                child for child in row["children"] if child["className"].startswith("package-version")
            )
            for row in result["rows"]
        }
        for package_id, expected in (
            ("test.unknown", "unknown"),
            ("test.red", "incompatible"),
            ("test.white", "compatible"),
        ):
            number = versions[package_id]["children"][0]
            self.assertIn(expected, number["className"], package_id)

    def test_the_state_chip_stays_right_and_the_verdict_sits_left_of_the_version(self) -> None:
        result = self._render(
            packages=[package("test.fine", [("1.0.0", "compatible")])],
            show_incompatible=True,
            selected="test.fine",
        )
        panel = result["detail"]
        top = panel["children"][0]
        self.assertEqual(top["className"], "detail-topline")
        self.assertEqual(
            [(child["className"].split() or [""])[0] for child in top["children"]],
            ["", "state-chip"],
            "标题行只有记录与状态 chip，状态 chip 在最右边",
        )
        self.assertNotIn("compatible", top["children"][-1]["className"])

        heading = next(
            child for child in panel["children"] if child["className"] == "detail-heading"
        )
        group = next(
            child for child in heading["children"] if child["className"] == "detail-version-group"
        )
        self.assertEqual([child["tag"] for child in group["children"]], ["span", "b"])
        self.assertIn("compatible", group["children"][0]["className"], "兼容 chip 在版本号左边")
        self.assertIn("detail-version", group["children"][1]["className"])

    def test_an_unusable_game_version_explains_the_empty_catalog(self) -> None:
        result = self._render(
            packages=[package("test.old", [("1.0.0", "incompatible")])],
            environment=environment(sprocket="0.127", state="legacy"),
        )

        self.assertIn("Sprocket 0.127 detected", " ".join(self._texts(result["notice"])))

    def test_the_sidebar_links_to_the_loader_install_when_missing(self) -> None:
        result = self._render(environment=environment(installed=False))

        # 文案来自 index.html 的 `data-i18n`（在 test_environment.py 里钉住），这里只看可见性。
        self.assertEqual(result["environment"]["install"]["tag"], "button")
        self.assertFalse(result["environment"]["install"]["hidden"])

    def test_the_statusbar_state_is_derived_not_remembered(self) -> None:
        """状态栏报当前状况：环境正常就正常，环境有问题立刻变红。"""
        healthy = self._render()["statusbar"]
        self.assertEqual(healthy["text"], "OK")
        self.assertIn("ready", healthy["mark"])

        broken = self._render(environment=environment(sprocket="0.2.54.2", conflict=True))["statusbar"]
        self.assertEqual(broken["text"], "Error")
        self.assertIn("error", broken["mark"])

    def test_a_corrupted_installed_file_keeps_the_statusbar_red(self) -> None:
        """持久的问题（文件损坏）留在状态栏上；一次操作失败只走 toast。"""
        broken = self._render(installed=[{
            "id": "test.fine", "name": "test.fine", "version": "1.0.0", "requested": True,
            "corrupted": True, "suppressed": False, "integrity": "corrupted", "files": [],
        }])["statusbar"]

        self.assertEqual(broken["text"], "Error")
        self.assertIn("error", broken["mark"])

    def test_the_sidebar_shows_both_versions(self) -> None:
        result = self._render()
        info = result["environment"]

        self.assertEqual(info["sprocket"]["text"], "Sprocket 0.2.53.2")
        self.assertEqual(info["loader"]["text"], "MelonLoader 0.7.3")
        self.assertTrue(info["note"]["hidden"], "没问题就不出那一行")

    def test_the_sidebar_shows_an_uninstalled_loader_as_a_link(self) -> None:
        result = self._render(environment=environment(installed=False))
        info = result["environment"]

        self.assertTrue(info["loader"]["hidden"], "未装时那一行只留链接")
        self.assertEqual(info["sprocket"]["text"], "Sprocket 0.2.53.2")
        self.assertFalse(info["install"]["hidden"])

    def test_an_environment_conflict_is_spelled_out_in_the_sidebar(self) -> None:
        result = self._render(environment=environment(sprocket="0.2.54.2", conflict=True))
        info = result["environment"]

        self.assertIn("error", info["sprocket"]["className"])
        self.assertFalse(info["note"]["hidden"])
        self.assertIn("error", info["note"]["className"])
        self.assertIn(
            "does not support Sprocket 0.2.54.2",
            " ".join(self._texts(info["note"])),
        )

    def test_an_unreadable_game_version_is_shown_raw_and_red(self) -> None:
        result = self._render(environment=environment(sprocket="0.127", state="legacy"))
        info = result["environment"]

        self.assertEqual(info["sprocket"]["text"], "Sprocket 0.127", "旧格式就显示原文")
        self.assertIn("error", info["sprocket"]["className"])
        self.assertIn("Sprocket 0.127 detected", " ".join(self._texts(info["note"])))


if __name__ == "__main__":
    unittest.main()
