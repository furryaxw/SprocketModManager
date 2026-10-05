"""在 Node 里真实跑一遍安装计划：版本选择器、改选后重新解析、入队带的版本。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "fixtures" / "client_ui" / "render_plan_harness.js"
CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
NODE = shutil.which("node")

PACKAGE = {
    "id": "test.mod",
    "name": "test.mod",
    "display_name": {"en": "Test Mod"},
    "description": {"en": ""},
    "authors": ["someone"],
    "repository": "test/repo",
    "repository_url": "https://github.com/test/repo",
    "license": "MIT",
    "category": "utility",
    "tags": [],
    "dependencies": [],
    "recommendations": [],
    "featured": False,
    "install_assets": ["TestMod.dll"],
    "installed": None,
    "release": {"tag": "v2.0.0", "version": "2.0.0", "verdict": "incompatible", "assets": []},
    "releases": [
        {
            "tag": "v2.0.0",
            "version": "2.0.0",
            "verdict": "incompatible",
            "compatibility": {"source": "declared"},
        },
        {
            "tag": "v1.0.0",
            "version": "1.0.0",
            "verdict": "compatible",
            "compatibility": {"source": "inherited", "from_tag": "v0.9.0"},
        },
    ],
}


def plan(version: str, dependencies: list[tuple[str, str]] = ()) -> dict:
    packages = [
        {
            "id": "test.mod",
            "display_name": {"en": "Test Mod"},
            "name": "test.mod",
            "version": version,
            "tag": f"v{version}",
            "assets": ["TestMod.dll"],
        }
    ]
    packages.extend(
        {
            "id": package_id,
            "display_name": {"en": package_id},
            "name": package_id,
            "version": dependency_version,
            "tag": f"v{dependency_version}",
            "assets": [f"{package_id}.dll"],
        }
        for package_id, dependency_version in dependencies
    )
    return {
        "id": "test.mod",
        "display_name": {"en": "Test Mod"},
        "name": "test.mod",
        "replaces_autotranslator": False,
        "packages": packages,
    }


def plan_for(package_id: str, version: str, display_name: str) -> dict:
    """给指定包的一项计划：多包场景里每个包各要一份（`plan()` 固定写死 test.mod）。"""
    return {
        "id": package_id,
        "display_name": {"en": display_name},
        "name": package_id,
        "replaces_autotranslator": False,
        "packages": [
            {
                "id": package_id,
                "display_name": {"en": display_name},
                "name": package_id,
                "version": version,
                "tag": f"v{version}",
                "assets": [f"{package_id}.dll"],
            }
        ],
    }


def package_for(package_id: str, display_name: str, versions: list[str]) -> dict:
    """目录里另一个包：名字与版本列表按需要给，其余照 `PACKAGE` 的样式。"""
    entry = json.loads(json.dumps(PACKAGE))
    entry["id"] = package_id
    entry["name"] = package_id
    entry["display_name"] = {"en": display_name}
    entry["releases"] = [
        {
            "tag": f"v{version}",
            "version": version,
            "verdict": "compatible",
            "compatibility": {"source": "declared"},
        }
        for version in versions
    ]
    entry["release"] = json.loads(json.dumps(entry["releases"][0]))
    return entry


class PlanSelectorHarnessTests(unittest.TestCase):
    def _run(self, **payload) -> dict:
        payload.setdefault("packages", [PACKAGE])
        payload.setdefault("install_ids", ["test.mod"])
        payload.setdefault("versions", {"test.mod": "2.0.0"})
        payload.setdefault("plan", {"plans": [plan("2.0.0")]})
        payload.setdefault("replan", {"plans": [plan("1.0.0")]})
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
        result = json.loads(completed.stdout or "{}")
        self.assertNotIn("error", result, result.get("error"))
        return result

    def test_the_root_package_offers_every_version_with_its_verdict(self) -> None:
        result = self._run()
        first = result["first"]

        self.assertEqual(
            [option["value"] for option in first["options"]], ["2.0.0", "1.0.0"]
        )
        self.assertIn("Compatible", first["options"][1]["text"], "选项文字里带三色标签")
        self.assertIn("Incompatible", first["options"][0]["text"])
        self.assertEqual(
            [option["value"] for option in first["options"] if option["selected"]], ["2.0.0"]
        )
        self.assertIn("incompatible", first["className"], "选择器跟着选中版本的颜色")

    def test_an_inherited_version_is_marked_with_a_star(self) -> None:
        result = self._run(change_to="1.0.0")

        texts = {option["value"]: option["text"] for option in result["first"]["options"]}
        self.assertEqual(texts["2.0.0"], "2.0.0 · Incompatible", "自己写了声明的不带星号")
        self.assertEqual(texts["1.0.0"], "1.0.0* · Compatible", "沿用更早声明的带星号")

    def test_changing_the_version_replans_that_package(self) -> None:
        result = self._run(change_to="1.0.0")

        self.assertEqual(
            result["planCalls"],
            [
                [["test.mod"], {"test.mod": "2.0.0"}, False],
                [["test.mod"], {"test.mod": "1.0.0"}, False],
            ],
            "改选之后按新版本单独重新解析",
        )
        self.assertEqual(
            [option["value"] for option in result["after"]["options"] if option["selected"]],
            ["1.0.0"],
        )

    def test_the_dependency_tree_is_redrawn_from_the_replanned_version(self) -> None:
        """改选之后画的必须是新那一版解析出来的树：依赖跟着版本换，不是把旧的留在原地。"""
        result = self._run(
            plan={"plans": [plan("2.0.0", [("test.api", "2.0.0")])]},
            replan={"plans": [plan("1.0.0", [("test.api", "1.0.0")])]},
            change_to="1.0.0",
        )

        self.assertEqual(result["first"]["lines"], ["Test Mod 2.0.0", "test.api 2.0.0"])
        self.assertEqual(result["after"]["lines"], ["Test Mod 1.0.0", "test.api 1.0.0"])

    def test_an_already_installed_target_still_opens_the_selector(self) -> None:
        """界面挑的那版＝装着的那版时后端会「跳过」；只要索引里还有别的版本，对话框就得照开。

        版本选择器是强行装新版唯一的路，被这一步挡掉的话那条路就整条断了。
        """
        result = self._run(
            versions={"test.mod": "1.0.0"},
            plan={"plans": []},
            skipped=["test.mod"],
            replan={"plans": [plan("1.0.0")]},
        )

        self.assertEqual(
            [option["value"] for option in result["first"]["options"]], ["2.0.0", "1.0.0"]
        )
        self.assertEqual(
            result["planCalls"][1], [["test.mod"], {"test.mod": "1.0.0"}, True],
            "第二次要计划时明确要求「装着的这版也给」",
        )

    def test_a_blocked_newer_version_can_be_forced_from_the_catalog(self) -> None:
        """已装到当前能装的那版、索引里还有个红版：对话框里选它、确认，它就得被强行入队。"""
        result = self._run(
            versions={"test.mod": "1.0.0"},
            skipped=["test.mod"],
            plans_sequence=[[], [plan("1.0.0")], [plan("2.0.0")]],
            change_to="2.0.0",
        )

        self.assertEqual([option["value"] for option in result["first"]["options"]], ["2.0.0", "1.0.0"])
        self.assertEqual(
            [option["value"] for option in result["after"]["options"] if option["selected"]], ["2.0.0"]
        )
        self.assertEqual(
            result["enqueue"][2], {"test.mod": "2.0.0"}, "红版按点名的那一版入队"
        )

    def test_a_failed_replan_keeps_the_box_with_the_reason_in_it(self) -> None:
        """依赖解不出来时不能把整个框换成一句报错：框留着、标红、写上原因，选择器也留着。"""
        reason = "test.lib >=9.9.9 has no release (test.lib has 1.0.0)"
        result = self._run(
            plan={"plans": [plan("2.0.0")]},
            replan={"plans": [], "failed": [{"id": "test.mod", "message": reason}]},
            change_to="1.0.0",
        )

        groups = result["after"]["groups"]
        self.assertEqual(len(groups), 1, "框还在")
        self.assertIn("skipped", groups[0]["className"].split(), "框整体按跳过标色")
        self.assertEqual(groups[0]["heading"], "Test Mod", "标题仍是模组名")
        self.assertEqual(groups[0]["mark"], "Skipped", "标题后跟「跳过」")
        self.assertEqual(groups[0]["reason"], f"Reason: {reason}", "原因写在框里")
        self.assertTrue(groups[0]["hasSelect"], "版本选择器留着 —— 换回能装的那一版只有这条路")
        self.assertEqual(groups[0]["lines"], ["Test Mod 1.0.0"], "树只剩根包那一行：这一版没解出来")

    def test_a_package_without_a_plan_still_gets_a_box(self) -> None:
        """后端只失败了其中几个包时，失败的那个也一样有自己的框，不能只剩一句报错。"""
        reason = "test.lib >=9.9.9 has no release"
        result = self._run(
            packages=[PACKAGE, package_for("test.other", "Other Mod", ["1.0.0"])],
            install_ids=["test.other", "test.mod"],
            versions={"test.other": "1.0.0", "test.mod": "2.0.0"},
            plan={
                "plans": [plan_for("test.other", "1.0.0", "Other Mod")],
                "failed": [{"id": "test.mod", "message": reason}],
            },
        )

        groups = result["first"]["groups"]
        self.assertEqual(
            [group["heading"] for group in groups], ["Other Mod", "Test Mod"], "框的顺序按用户点的先后"
        )
        self.assertNotIn("skipped", groups[0]["className"].split())
        self.assertIn("skipped", groups[1]["className"].split())
        self.assertEqual(groups[1]["mark"], "Skipped")
        self.assertEqual(groups[1]["reason"], f"Reason: {reason}")
        self.assertEqual(
            groups[1]["lines"], ["Test Mod 2.0.0"],
            "框里那一行写的是用户点名的那一版，不是别的东西",
        )

    def test_switching_back_to_an_installable_version_restores_the_box(self) -> None:
        """先改到装不了的那一版、再改回能装的：框要照旧，报错与红标题都要消失。"""
        reason = "test.lib >=9.9.9 has no release"
        result = self._run(
            plans_sequence=[[plan("2.0.0")], [], [plan("2.0.0", [("test.api", "2.0.0")])]],
            failed_sequence=[[], [{"id": "test.mod", "message": reason}], []],
            changes=["1.0.0", "2.0.0"],
        )

        broken = result["steps"][0]["groups"]
        self.assertIn("skipped", broken[0]["className"].split())
        self.assertEqual(broken[0]["reason"], f"Reason: {reason}")

        restored = result["steps"][1]["groups"]
        self.assertEqual(len(restored), 1, "框还在 —— 它没有被跳过那一步删掉")
        self.assertNotIn("skipped", restored[0]["className"].split(), "换回能装的版本后报错要消失")
        self.assertEqual(restored[0]["reason"], "")
        self.assertEqual(restored[0]["mark"], "")
        self.assertEqual(restored[0]["lines"], ["Test Mod 2.0.0", "test.api 2.0.0"])

    def test_the_confirmed_install_carries_the_chosen_version(self) -> None:
        result = self._run(change_to="1.0.0")

        self.assertIsNotNone(result["enqueue"], "确认后应该入队")
        self.assertEqual(result["enqueue"][0], ["test.mod"])
        self.assertEqual(result["enqueue"][2], {"test.mod": "1.0.0"}, "入队带的是用户选的那个版本")

    def test_a_package_without_a_version_list_keeps_the_plain_line(self) -> None:
        # 索引里没带版本列表（拿不到可选项）时不出选择器，行里照旧只写版本号。
        payload = json.loads(json.dumps(PACKAGE))
        payload["releases"] = []
        result = self._run(packages=[payload], plan={"plans": [plan("2.0.0")]})

        self.assertEqual(result["first"]["options"], [], "拿不到版本列表就没有选择器")
        self.assertEqual(result["enqueue"][2], {"test.mod": "2.0.0"})

    def test_the_package_list_drives_the_options(self) -> None:
        # 索引里给几个版本就列几个：改动版本列表不需要改 UI 代码。
        payload = json.loads(json.dumps(PACKAGE))
        payload["releases"] = payload["releases"][:1]
        result = self._run(packages=[payload])

        self.assertEqual([option["value"] for option in result["first"]["options"]], ["2.0.0"])


    def test_a_displaced_loader_is_listed_in_the_confirmation(self) -> None:
        """装一个供给同一项能力的加载器时，确认框先说明旧的那个会被交还。"""
        planned = plan("2.0.0")
        planned["displaces"] = [
            {
                "id": "lavagang.melonloader",
                "name": "MelonLoader",
                "display_name": {"en": "MelonLoader"},
                "version": "0.7.3",
            }
        ]
        result = self._run(plan={"plans": [planned]})

        self.assertEqual(
            result["first"]["warnings"],
            [
                "Removes MelonLoader: it provides the same capability as this loader, "
                "and its files are backed up first."
            ],
        )

    def test_a_failing_plan_reports_exactly_once(self) -> None:
        """点一次安装只该报一次：重复的监听/重复的调用都会让这句话弹两遍。"""
        message = "no compatible release set found (test.mod =1.0.0; available releases: none)"
        result = self._run(plan_failure=message)

        self.assertEqual(len(result["planCalls"]), 1, "只解析一次")
        self.assertEqual(len(result["toasts"]), 1, "只弹一条提示")
        # 界面按 code 说人话，后端原文仍然带出来（排查用）。
        self.assertIn("No installable version set was found", result["toasts"][0])
        self.assertIn(message, result["toasts"][0])


class SkippedPlanStyleTests(unittest.TestCase):
    """解析不了的模组的样式契约：框标红、标题跟着红 —— 这是它在界面上唯一的可见信号。"""

    def setUp(self) -> None:
        self.css = (CLIENT_UI / "app.css").read_text(encoding="utf-8")

    def _rule(self, selector: str) -> str:
        match = re.compile(rf"^{re.escape(selector)} \{{", re.MULTILINE).search(self.css)
        self.assertIsNotNone(match, f"CSS 里没有 {selector} 规则")
        start = match.start()
        return self.css[start:self.css.index("}", start)]

    def test_the_skipped_box_and_its_heading_are_red(self) -> None:
        self.assertIn("border-color: var(--danger)", self._rule(".plan-group.skipped"))
        self.assertIn("color: var(--danger)", self._rule(".plan-group.skipped strong"))

    def test_the_skipped_word_sits_on_the_heading_line(self) -> None:
        """「跳过」跟着标题后面同一行：标题保持块级的话它就掉到下一行去了。"""
        self.assertIn("display: inline-block", self._rule(".plan-group.skipped strong"))
        self.assertIn("display: inline", self._rule(".plan-skipped-mark"))

    def test_the_reason_wraps_instead_of_being_clipped(self) -> None:
        """原因可能很长（求解器把缺失的区间与包实际有哪些版本都写进去），要能换行。"""
        self.assertIn("white-space: normal", self._rule(".plan-reason"))


if __name__ == "__main__":
    unittest.main()
