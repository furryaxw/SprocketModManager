"""在 Node 里真实跑一遍安装计划：版本选择器、改选后重新解析、入队带的版本。"""

from __future__ import annotations

import json
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


def plan(version: str) -> dict:
    return {
        "id": "test.mod",
        "display_name": {"en": "Test Mod"},
        "name": "test.mod",
        "replaces_autotranslator": False,
        "packages": [
            {
                "id": "test.mod",
                "display_name": {"en": "Test Mod"},
                "name": "test.mod",
                "version": version,
                "tag": f"v{version}",
                "assets": ["TestMod.dll"],
            }
        ],
    }


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
            result["enqueue"][3], {"test.mod": "2.0.0"}, "红版按点名的那一版入队"
        )

    def test_the_confirmed_install_carries_the_chosen_version(self) -> None:
        result = self._run(change_to="1.0.0")

        self.assertIsNotNone(result["enqueue"], "确认后应该入队")
        self.assertEqual(result["enqueue"][0], ["test.mod"])
        self.assertEqual(result["enqueue"][3], {"test.mod": "1.0.0"}, "入队带的是用户选的那个版本")

    def test_a_package_without_a_version_list_keeps_the_plain_line(self) -> None:
        # 索引里没带版本列表（拿不到可选项）时不出选择器，行里照旧只写版本号。
        payload = json.loads(json.dumps(PACKAGE))
        payload["releases"] = []
        result = self._run(packages=[payload], plan={"plans": [plan("2.0.0")]})

        self.assertEqual(result["first"]["options"], [], "拿不到版本列表就没有选择器")
        self.assertEqual(result["enqueue"][3], {"test.mod": "2.0.0"})

    def test_the_package_list_drives_the_options(self) -> None:
        # 索引里给几个版本就列几个：改动版本列表不需要改 UI 代码。
        payload = json.loads(json.dumps(PACKAGE))
        payload["releases"] = payload["releases"][:1]
        result = self._run(packages=[payload])

        self.assertEqual([option["value"] for option in result["first"]["options"]], ["2.0.0"])


    def test_a_failing_plan_reports_exactly_once(self) -> None:
        """点一次安装只该报一次：重复的监听/重复的调用都会让这句话弹两遍。"""
        message = "no compatible release set found (test.mod =1.0.0; available releases: none)"
        result = self._run(plan_failure=message)

        self.assertEqual(len(result["planCalls"]), 1, "只解析一次")
        self.assertEqual(len(result["toasts"]), 1, "只弹一条提示")
        # 界面按 code 说人话，后端原文仍然带出来（排查用）。
        self.assertIn("No installable version set was found", result["toasts"][0])
        self.assertIn(message, result["toasts"][0])


if __name__ == "__main__":
    unittest.main()
