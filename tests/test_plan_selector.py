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


def line(package_id: str, version: str, display_name: str = "Test Mod", **extra) -> dict:
    """计划里的一行：装这一版。候选版本由目录读数给，这一行只报选中了哪一版。"""
    entry = {
        "id": package_id,
        "name": package_id,
        "display_name": {"en": display_name},
        "installed_version": "",
        "named": True,
        "action": "install",
        "version": version,
        "reinstall": False,
        "verdict": "",
        "problems": [],
        "depends_on": [],
        "required_by": [],
        "remove_with": [],
        "versions": [],
    }
    entry.update(extra)
    return entry


def recommendation(package_id: str, version: str = "1.0.0") -> dict:
    """计划里的一条推荐：列在计划下面，勾中才会跟着一起装。"""
    return {
        "id": package_id,
        "name": package_id,
        "display_name": {"en": package_id},
        "version": version,
        "recommended_by": ["test.mod"],
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
        payload.setdefault("lines", [line("test.mod", "2.0.0")])
        payload.setdefault("replan_lines", [line("test.mod", "1.0.0")])
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
            [option["value"] for option in first["options"]],
            ["2.0.0", "1.0.0", "skip"],
            "全部候选版本，末尾那项是「不安装」",
        )
        self.assertIn("Compatible", first["options"][1]["text"], "选项文字里带三色标签")
        self.assertIn("Incompatible", first["options"][0]["text"])
        self.assertEqual(
            [option["value"] for option in first["options"] if option["selected"]], ["2.0.0"]
        )

    def test_an_inherited_version_is_marked_with_a_star(self) -> None:
        result = self._run(change_to="1.0.0")

        texts = {option["value"]: option["text"] for option in result["first"]["options"]}
        self.assertEqual(texts["2.0.0"], "2.0.0 · Incompatible", "自己写了声明的不带星号")
        self.assertEqual(texts["1.0.0"], "1.0.0* · Compatible", "沿用更早声明的带星号")

    def test_changing_the_version_replans_the_whole_batch(self) -> None:
        result = self._run(change_to="1.0.0")

        self.assertEqual(
            result["planCalls"],
            [
                [["test.mod"], {}, False, False],
                [["test.mod"], {"test.mod": "1.0.0"}, False, False],
            ],
            "改选之后按新的选择重解这一批",
        )
        self.assertEqual(
            [option["value"] for option in result["after"]["options"] if option["selected"]],
            ["1.0.0"],
        )

    def test_what_gets_installed_follows_the_replanned_version(self) -> None:
        """改选之后会装的必须是新那一版解出来的那些：依赖跟着版本换，不是把旧的留在原地。"""
        result = self._run(
            install_extra={"test.api": "2.0.0"},
            replan_install_extra={"test.api": "1.0.0"},
            change_to="1.0.0",
        )

        self.assertEqual(result["first"]["install"], {"test.mod": "2.0.0", "test.api": "2.0.0"})
        self.assertEqual(result["after"]["install"], {"test.mod": "1.0.0", "test.api": "1.0.0"})

    def test_an_already_installed_target_still_opens_the_plan_page(self) -> None:
        """界面挑的那版＝装着的那版时计划页照开：下拉里全部版本都在，换版本这条路不能断。

        `reinstall` 标记是「已装同一版也给」的表达方式，重装按钮就是用它。
        """
        result = self._run(reinstall=True)

        self.assertEqual(
            [option["value"] for option in result["first"]["options"]],
            ["2.0.0", "1.0.0", "skip"],
        )
        self.assertEqual(result["planCalls"][0][2], True, "重装这个标记照实交给后端")

    def test_a_blocked_newer_version_can_be_chosen_from_the_catalog(self) -> None:
        """已装到当前能装的那版、索引里还有个红版：下拉里选它、确认，它就按点名的那一版执行。"""
        result = self._run(
            lines_sequence=[
                [line("test.mod", "1.0.0")],
                [line("test.mod", "2.0.0")],
            ],
            change_to="2.0.0",
        )

        self.assertEqual(
            [option["value"] for option in result["first"]["options"]], ["2.0.0", "1.0.0", "skip"]
        )
        self.assertEqual(
            [option["value"] for option in result["after"]["options"] if option["selected"]], ["2.0.0"]
        )
        self.assertEqual(
            result["enqueue"][1], {"test.mod": "2.0.0"}, "红版按点名的那一版执行"
        )

    def test_every_package_in_the_plan_gets_the_same_card_with_a_select(self) -> None:
        """点名的包与跟着进来的依赖是同一种卡片：每张都有自己的版本下拉。"""
        result = self._run(
            packages=[PACKAGE, package_for("test.api", "API", ["2.0.0", "1.0.0"])],
            install_ids=["test.mod"],
            lines=[
                line("test.mod", "2.0.0"),
                line("test.api", "2.0.0", "API", named=False),
            ],
        )

        groups = result["first"]["groups"]
        self.assertEqual([group["heading"] for group in groups], ["Test Mod", "API"])
        self.assertEqual([group["hasSelect"] for group in groups], [True, True], "依赖那一行也能改版本")

    def test_changing_a_dependency_version_puts_it_in_the_named_batch(self) -> None:
        """改了依赖的版本：那一行从此刻起按点名的包算，整批按新钉住的版本重解。"""
        result = self._run(
            packages=[PACKAGE, package_for("test.api", "API", ["2.0.0", "1.0.0"])],
            install_ids=["test.mod"],
            lines=[
                line("test.mod", "2.0.0"),
                line("test.api", "2.0.0", "API", named=False),
            ],
            change_to="1.0.0",
            change_select=1,
        )

        self.assertEqual(
            result["planCalls"],
            [
                [["test.mod"], {}, False, False],
                [["test.mod", "test.api"], {"test.api": "1.0.0"}, False, False],
            ],
            "改过的那一行跟着一起送进求解器",
        )

    def test_the_title_shares_the_row_with_the_version_select(self) -> None:
        result = self._run()

        group = result["first"]["groups"][0]
        self.assertTrue(group["titleInLine"], "标题与版本下拉同一行")
        self.assertTrue(group["hasSelect"])

    def test_the_card_names_its_dependencies_and_dependents(self) -> None:
        result = self._run(
            packages=[PACKAGE, package_for("test.api", "API", ["2.0.0"])],
            install_ids=["test.mod"],
            lines=[line("test.mod", "2.0.0", depends_on=["test.api"], required_by=["test.host"])],
        )

        reasons = result["first"]["groups"][0]["reasons"]
        self.assertIn("Depends on API", reasons, "依赖写在标题行下面，名字用目录里的那个")
        self.assertIn("Required by test.host", reasons, "被谁依赖也写出来，认不出就写 id")

    def test_the_plan_lists_the_recommended_packages(self) -> None:
        result = self._run(recommendations=[recommendation("test.extra")])

        group = result["first"]["groups"][-1]
        self.assertIn("recommendation-group", group["className"], "推荐自成一块，跟在计划后面")
        self.assertEqual(group["heading"], "Install recommended mods too")

    def test_checked_recommendations_join_the_confirmed_batch(self) -> None:
        result = self._run(
            recommendations=[recommendation("test.extra")],
            check_recommendations=["test.extra"],
        )

        self.assertEqual(
            result["enqueue"][0], ["test.mod", "test.extra"], "勾中的推荐跟着这一批一起执行"
        )
        self.assertEqual(result["enqueue"][1], {}, "它没有单独的选择，版本由求解器定")

    def test_an_unchecked_recommendation_stays_out_of_the_batch(self) -> None:
        result = self._run(recommendations=[recommendation("test.extra")])

        self.assertEqual(result["enqueue"][0], ["test.mod"], "没勾就不装")

    def test_a_named_version_that_cannot_coexist_says_so_in_the_ui_language(self) -> None:
        """凑不出有效解时留着上一个计划，原因那句话由界面按当前语言写。"""
        result = self._run(
            lines_sequence=[[line("test.mod", "2.0.0")], []],
            no_solution_sequence=[False, True],
            no_solution_message="backend english sentence",
            no_solution_roots=[{"id": "test.mod", "range": "=1.0.0"}],
            change_to="1.0.0",
        )

        self.assertEqual(
            result["steps"][0]["messages"],
            ["No version set satisfies every named version: Test Mod =1.0.0"],
        )

    def test_a_row_without_any_installable_version_says_so_in_the_ui_language(self) -> None:
        """没有可装的版本时 `others` 是空的，那句话也要由界面写，不能漏出后端那句英文。"""
        result = self._run(
            lines=[
                line(
                    "test.mod", None,
                    action="skip",
                    problems=[{
                        "kind": "no-candidate",
                        "others": [],
                        "text": "test.mod is not in the registry",
                    }],
                ),
            ],
        )

        self.assertEqual(
            result["first"]["groups"][0]["reasons"],
            ["Reason: No installable version in the registry: test.mod"],
        )

    def test_the_reinstall_mark_sits_left_of_the_version_select(self) -> None:
        result = self._run(lines=[line("test.mod", "2.0.0", reinstall=True)])

        group = result["first"]["groups"][0]
        self.assertEqual(group["mark"], "Reinstall", "重装这一行才挂标记")
        self.assertTrue(group["markBeforeSelect"], "标记在下拉左侧")

    def test_a_failed_replan_keeps_the_box_with_the_reason_in_it(self) -> None:
        """解不出来时不能把整个框换成一句报错：框留着、标红、写上原因，选择器也留着。"""
        reason = "test.lib >=9.9.9 has no release (test.lib has 1.0.0)"
        result = self._run(
            lines=[line("test.mod", "2.0.0")],
            replan_lines=[],
            replan_failed=[{"id": "test.mod", "message": reason}],
            change_to="1.0.0",
        )

        groups = result["after"]["groups"]
        self.assertEqual(len(groups), 1, "框还在")
        self.assertIn("skipped", groups[0]["className"].split(), "框整体按跳过标色")
        self.assertEqual(groups[0]["heading"], "Test Mod", "标题仍是模组名")
        self.assertEqual(groups[0]["mark"], "", "标记只说重装：不安装由下拉自己写着")
        self.assertEqual(
            [option["value"] for option in result["after"]["options"] if option["selected"]],
            ["skip"],
            "下拉里选中的就是「不安装」",
        )
        self.assertEqual(groups[0]["reason"], f"Reason: {reason}", "原因写在框里")
        self.assertTrue(groups[0]["hasSelect"], "版本选择器留着 —— 换回能装的那一版只有这条路")

    def test_a_package_without_a_plan_still_gets_a_box(self) -> None:
        """后端只解不出其中几个包时，那个包也一样有自己的框，不能只剩一句报错。"""
        reason = "test.lib >=9.9.9 has no release"
        result = self._run(
            packages=[PACKAGE, package_for("test.other", "Other Mod", ["1.0.0"])],
            install_ids=["test.other", "test.mod"],
            lines=[line("test.other", "1.0.0", "Other Mod")],
            failed=[{"id": "test.mod", "message": reason}],
        )

        groups = result["first"]["groups"]
        self.assertEqual(
            [group["heading"] for group in groups], ["Other Mod", "Test Mod"], "框的顺序按用户点的先后"
        )
        self.assertNotIn("skipped", groups[0]["className"].split())
        self.assertIn("skipped", groups[1]["className"].split())
        self.assertEqual(groups[1]["mark"], "", "出局的包不挂标记：下拉里已经写着「不安装」")
        self.assertEqual(groups[1]["reason"], f"Reason: {reason}")

    def test_switching_back_to_an_installable_version_restores_the_box(self) -> None:
        """先改到装不了的那一版、再改回能装的：框要照旧，报错与红标题都要消失。"""
        reason = "test.lib >=9.9.9 has no release"
        result = self._run(
            lines_sequence=[[line("test.mod", "2.0.0")], [], [line("test.mod", "2.0.0")]],
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

    def test_the_confirmed_install_carries_the_chosen_version(self) -> None:
        result = self._run(change_to="1.0.0")

        self.assertIsNotNone(result["enqueue"], "确认后应该执行")
        self.assertEqual(result["enqueue"][0], ["test.mod"])
        self.assertEqual(result["enqueue"][1], {"test.mod": "1.0.0"}, "执行带的是用户选的那个版本")

    def test_a_package_without_a_version_list_keeps_the_plain_line(self) -> None:
        # 索引里没带版本列表（拿不到可选项）时下拉里只剩「不安装」这一项。
        payload = json.loads(json.dumps(PACKAGE))
        payload["releases"] = []
        result = self._run(packages=[payload])

        self.assertEqual(
            [option["value"] for option in result["first"]["options"]], ["skip"],
            "拿不到版本列表时只剩「不安装」",
        )
        self.assertEqual(result["enqueue"][0], ["test.mod"])
        self.assertEqual(result["enqueue"][1], {}, "没改过选择就不带覆盖：版本由后端那一份计划给")

    def test_the_package_list_drives_the_options(self) -> None:
        # 索引里给几个版本就列几个：改动版本列表不需要改 UI 代码。
        payload = json.loads(json.dumps(PACKAGE))
        payload["releases"] = payload["releases"][:1]
        result = self._run(packages=[payload])

        self.assertEqual(
            [option["value"] for option in result["first"]["options"]], ["2.0.0", "skip"]
        )

    def test_the_replacements_are_listed_in_the_confirmation(self) -> None:
        """为了让卸载目标走得掉而换版本的依赖者，确认框先把它摆出来。"""
        result = self._run(
            install_ids=["test.mod"],
            lines=[line("test.mod", "2.0.0", action="remove", installed_version="2.0.0")],
            replaced_by=[{"id": "test.other", "version": "1.0.0"}],
        )

        self.assertEqual(len(result["first"]["warnings"]), 1)
        self.assertIn("test.other 1.0.0", result["first"]["warnings"][0])

    def test_a_failing_plan_reports_exactly_once(self) -> None:
        """点一次安装只该报一次：重复的监听/重复的调用都会让这句话弹两遍。"""
        message = "no compatible release set found (test.mod =1.0.0; available releases: none)"
        result = self._run(plan_failure=message)

        self.assertEqual(len(result["planCalls"]), 1, "只解一次")
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

    def test_the_mark_sits_on_the_heading_line(self) -> None:
        """标题、标记与版本下拉同处一行：`.plan-line` 是 flex 行，标题占满左边。"""
        self.assertIn("display: flex", self._rule(".plan-line"))
        self.assertIn("flex: 1", self._rule(".plan-line strong"))
        self.assertIn("display: inline", self._rule(".plan-skipped-mark"))

    def test_the_reason_wraps_instead_of_being_clipped(self) -> None:
        """原因可能很长（求解器把缺失的区间与包实际有哪些版本都写进去），要能换行。"""
        self.assertIn("white-space: normal", self._rule(".plan-reason"))


if __name__ == "__main__":
    unittest.main()
