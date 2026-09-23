"""在 Node 里真实执行客户端「已安装」页的渲染逻辑。

用最小 DOM 注入运行仓库里未修改的 `installs.js`（见 `fixtures/client_ui/render_installed_harness.js`），
断言真实 `get_installed` payload（含 `local_mods`）会被渲染成正确的名称、芯片与启用/禁用按钮，
并且按钮真的调用 `toggle_mod`。

这不是打包 WebView 的人工验收，但比"字符串存在性"断言强得多；没有 node 时自动跳过。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "fixtures" / "client_ui" / "render_installed_harness.js"
CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
NODE = shutil.which("node")


def payload(
        corrupted: bool = False,
        missing: bool = False,
        suppressed: bool = False,
        packages: list | None = None,
        selection: list | None = None,
        action: str = "",
        filter_key: str = "",
        click_rows: list | None = None,
        dblclick_rows: list | None = None,
        context_rows: list | None = None,
        dblclick_row_buttons: list | None = None,
        environment: dict | None = None,
) -> dict:
    """纯扫描模型的 payload：列表以 `local_mods` 为准，`installed` 只提供归属标记。"""
    integrity = "corrupted" if corrupted else "suppressed" if suppressed else "release"
    installed_record = {
        "id": "furryaxw.sprocket-laser-rangefinder", "name": "SprocketLaserRangefinder", "version": "0.1.3",
        "requested": True, "corrupted": corrupted, "suppressed": suppressed, "integrity": integrity,
        "files": ["Mods/SprocketLaserRangefinder.dll"],
    }
    first_mod_dependencies = ["SprocketDepth"] if missing else []
    document = {
        "installed": [installed_record],
        "unrecognized": [],
        "local_mods": [
            {
                "path": "Mods/SprocketLaserRangefinder.dll",
                "name": "SprocketLaserRangefinder.dll",
                "display_name": "Sprocket Laser Rangefinder",
                "version": "0.1.3",
                "authors": ["furryAxw"],
                "kind": "Mods",
                "disabled": False,
                "registry_id": "furryaxw.sprocket-laser-rangefinder",
                "registry_match": "declared-id",
                "registry_display_name": {"en": "Sprocket Laser Rangefinder", "zh": "Sprocket 激光测距仪"},
                "registry_description": {"en": "Laser rangefinder.", "zh": "激光测距仪。"},
                "required_dependencies": ["SprocketModAPI"],
                "missing_dependencies": first_mod_dependencies,
                "incompatible_assemblies": [],
                "installed_package_id": "furryaxw.sprocket-laser-rangefinder",
                "assembly_name": "SprocketLaserRangefinder",
                "sha256": "",
                "error": "",
            },
            {
                "path": "Mods/CannonSoundPoolFix.dll.disable",
                "name": "CannonSoundPoolFix.dll.disable",
                "display_name": "Cannon Sound Pool Fix",
                "version": "1.2.0",
                "authors": ["furryAxw"],
                "kind": "Mods",
                "disabled": True,
                "registry_id": "furryaxw.cannon-sound-pool-fix",
                "registry_match": "declared-id",
                "required_dependencies": [],
                "incompatible_assemblies": [],
                "installed_package_id": "",
                "assembly_name": "CannonSoundPoolFix",
                "sha256": "",
                "error": "",
            },
            {
                "path": "UserLibs/UniverseLib.ML.IL2CPP.Interop.dll",
                "name": "UniverseLib.ML.IL2CPP.Interop.dll",
                "display_name": "UniverseLib",
                "version": "1.6.2.0",
                "authors": ["Sinai"],
                "kind": "UserLibs",
                "disabled": False,
                "registry_id": "",
                "registry_match": "",
                "required_dependencies": [],
                "incompatible_assemblies": ["LegacyOverhaul"],
                "installed_package_id": "",
                "assembly_name": "UniverseLib.ML.IL2CPP.Interop",
                "sha256": "",
                "error": "",
            },
        ],
        "local_summary": {"total": 3, "disabled": 1, "registry_matched": 2, "unmanaged": 2, "unreadable": 0,
                          "missing_dependencies": 1 if missing else 0},
        "has_any_mods": True,
    }
    document["packages"] = packages or []
    if environment is not None:
        document["environment"] = environment
    if selection is not None:
        document["selection"] = selection
    if action:
        document["action"] = action
    if filter_key:
        document["filter"] = filter_key
    if click_rows is not None:
        document["clickRows"] = click_rows
    if dblclick_rows is not None:
        document["dblclickRows"] = dblclick_rows
    if context_rows is not None:
        document["contextRows"] = context_rows
    if dblclick_row_buttons is not None:
        document["dblclickRowButtons"] = dblclick_row_buttons
    return document


def _find(node, predicate):
    """在序列化后的行 DOM 里找第一个满足条件的节点。"""
    if predicate(node):
        return node
    for child in node["children"]:
        found = _find(child, predicate)
        if found is not None:
            return found
    return None


def open_location_calls(result: dict) -> list:
    """该次渲染里真实发出的 `open_mod_location` 参数（`focus` 之类的事件不带 `args`）。"""
    return [
        entry["args"]
        for entry in result["apiCalls"]
        if entry.get("kind") == "call" and entry["args"][0] == "open_mod_location"
    ]


def focus_calls(result: dict) -> list:
    return [entry["id"] for entry in result["apiCalls"] if entry.get("kind") == "focus"]


@unittest.skipIf(NODE is None, "node is not available")
class InstalledRenderHarnessTests(unittest.TestCase):
    def _render(self, **options) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "payload.json"
            payload_path.write_text(json.dumps(payload(**options), ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(HARNESS), str(CLIENT_UI), str(payload_path)],
                capture_output=True,
                text=True,
                # 行里会出现中文（i18n 断言），必须显式用 UTF-8 解码，
                # 否则中文 Windows 的 GBK 默认解码会让读线程崩掉、stdout 变成 None。
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, (completed.stdout or "") + (completed.stderr or ""))
            return json.loads(completed.stdout or "{}")

    def test_renders_real_names_chips_and_requires(self) -> None:
        result = self._render()
        self.assertEqual(result["count"], "3 detected mods")
        rows = result["rows"]
        self.assertEqual(len(rows), 3, "one row per DLL on disk")

        def flatten(node):
            texts = [node["text"]] if node["text"] else []
            for child in node["children"]:
                texts.extend(flatten(child))
            return texts

        installed_text = " | ".join(flatten(rows[0]))
        self.assertIn("Sprocket 激光测距仪", installed_text,
                      "a recognized mod uses the cached registry's localized name (i18n)")
        self.assertNotIn("Sprocket Laser Rangefinder", installed_text,
                         "the DLL's English-only name is not used when a localized one exists")
        self.assertIn("0.1.3", installed_text)
        self.assertIn("User-installed", installed_text)
        self.assertIn("SprocketModAPI", installed_text, "required dependencies must be rendered")
        self.assertIn("Requires", installed_text)
        self.assertIn("Mods", installed_text)

        disabled_text = " | ".join(flatten(rows[1]))
        self.assertIn("Cannon Sound Pool Fix", disabled_text)
        self.assertIn("1.2.0", disabled_text)
        self.assertIn("Enable", disabled_text, "按钮显示反向动作（启用），状态仍然看得出来")
        self.assertIn("Mods", disabled_text)

        third_text = " | ".join(flatten(rows[2]))
        self.assertIn("UniverseLib", third_text)
        self.assertIn("Local only", third_text, "mods without an install record are marked as local")
        self.assertIn("Incompatible: LegacyOverhaul", third_text)
        self.assertIn("UserLibs", third_text)

    def test_kind_chip_precedes_the_action_button(self) -> None:
        result = self._render()
        disabled_actions = result["rows"][1]["children"][2]
        labels = [child["text"] for child in disabled_actions["children"]]
        self.assertEqual(labels, ["Mods", "Enable"],
                         "kind chip, then the action button")

    def test_every_row_starts_with_a_selection_checkbox(self) -> None:
        result = self._render()
        for row in result["rows"]:
            self.assertEqual(row["className"], "data-row selectable")
            checkbox = row["children"][0]
            self.assertEqual(checkbox["tag"], "input")
            self.assertIn("package-check", checkbox["className"])
            self.assertFalse(checkbox["checked"])

    def test_selected_row_is_marked_and_selects_its_key(self) -> None:
        result = self._render(selection=["Mods/CannonSoundPoolFix.dll.disable"])
        selected = result["rows"][1]
        self.assertIn("selected", selected["className"])
        self.assertTrue(selected["children"][0]["checked"])

    def test_corrupted_row_shows_chip_and_reinstall(self) -> None:
        """已损坏状态：芯片排在形态之前，并提供「重装/更新」和「抑制提示」。"""
        result = self._render(corrupted=True)
        actions = result["rows"][0]["children"][2]
        labels = [child["text"] for child in actions["children"]]
        self.assertEqual(labels, ["Corrupted", "Mods", "Disable", "Reinstall", "Mute warning", "Remove"],
                         "损坏芯片在形态芯片之前，重装/抑制按钮在卸载之前")
        reinstall = next(child for child in actions["children"] if child["text"] == "Reinstall")
        self.assertTrue(reinstall["disabled"], "注册表里没有这个包时不能假装能重装")
        mute = next(child for child in actions["children"] if child["text"] == "Mute warning")
        self.assertIs(mute["disabled"], False, "抑制是纯本地开关，随时可用")

    def test_suppressed_row_has_no_chip_but_still_offers_the_unmute_button(self) -> None:
        """被抑制的行不出芯片，但仍靠「取消抑制」按钮区别于正常行。"""
        result = self._render(suppressed=True)
        actions = result["rows"][0]["children"][2]
        labels = [child["text"] for child in actions["children"]]
        self.assertEqual(labels, ["Mods", "Disable", "Unmute warning", "Remove"])

    def test_newer_release_shows_a_chip_while_an_equal_one_does_not(self) -> None:
        """新版本提示只对「发布版本比安装记录新」的行出芯片。"""
        package = {"id": "furryaxw.sprocket-laser-rangefinder", "release": {"version": "0.2.0"}}
        newer = [child["text"] for child in self._render(packages=[package])["rows"][0]["children"][2]["children"]]
        self.assertIn("Version 0.2.0 available", newer, "0.1.3 → 0.2.0 是新版本")

        package["release"]["version"] = "0.1.3"
        equal = [child["text"] for child in self._render(packages=[package])["rows"][0]["children"][2]["children"]]
        self.assertNotIn("Version 0.1.3 available", equal, "版本相同不算新版本")

        library = self._render(packages=[{"id": "sinai.universelib", "release": {"version": "2.0.0"}}])
        third = [child["text"] for child in library["rows"][2]["children"][2]["children"]]
        self.assertNotIn("Version 2.0.0 available", third, "纯本地库没有安装记录，不该报更新")

    def test_suppress_button_calls_the_api_with_the_path(self) -> None:
        result = self._render(corrupted=True)
        self.assertIn("Mute warning", result["clickedButtons"])
        calls = [
            entry["args"]
            for entry in result["apiCalls"]
            if entry["kind"] == "call" and entry["args"][0] == "set_integrity_suppressed"
        ]
        self.assertEqual(calls, [["set_integrity_suppressed", "Mods/SprocketLaserRangefinder.dll", True]])
        self.assertEqual([entry for entry in result["apiCalls"] if entry["kind"] == "error"], [])

    def test_missing_dependency_is_rendered_from_local_metadata(self) -> None:
        """依赖缺口来自 DLL 元数据，行里要看得见，标题也要给总数。"""
        result = self._render(missing=True)
        row_text = " | ".join(child["text"] for child in result["rows"][0]["children"][1]["children"])
        self.assertIn("Missing: SprocketDepth", row_text)
        self.assertIn("Requires: SprocketModAPI", row_text, "已满足的依赖仍然显示")
        self.assertIn("1 missing deps", result["count"], "标题要给出依赖缺口数量")

    def test_toggle_buttons_call_the_api_with_the_right_target_state(self) -> None:
        result = self._render()
        self.assertEqual(result["clickedButtons"], ["Disable", "Enable"],
            "the enabled row offers Disable and the disabled row offers Enable")
        toggles = [entry["args"] for entry in result["apiCalls"] if entry["kind"] == "call" and entry["args"][0] == "toggle_mod"]
        self.assertEqual(
            toggles,
            [
                ["toggle_mod", "Mods/SprocketLaserRangefinder.dll", False],
                ["toggle_mod", "Mods/CannonSoundPoolFix.dll.disable", True],
            ],
            "each button must pass its own path and the inverted target state",
        )
        self.assertEqual([entry for entry in result["apiCalls"] if entry["kind"] == "error"], [])

    def test_batch_buttons_stay_disabled_without_a_selection(self) -> None:
        toolbar = self._render()["toolbar"]
        self.assertEqual(toolbar["selection"], "")
        self.assertTrue(toolbar["barHidden"], "没选东西时底部操作栏不出现")
        self.assertEqual(toolbar["toggle"]["text"], "Select all")
        self.assertFalse(toolbar["toggle"]["disabled"], "列表里有行就能全选")
        self.assertFalse(toolbar["invertDisabled"])
        self.assertEqual(
            toolbar["buttons"],
            {"update-selected": True, "disable-selected": True, "enable-selected": True, "remove-selected": True},
            "没选东西时批量按钮不可用",
        )

    def test_select_all_is_unavailable_when_the_filter_matches_nothing(self) -> None:
        toolbar = self._render(filter_key="outdated")["toolbar"]
        self.assertTrue(toolbar["toggle"]["disabled"])
        self.assertTrue(toolbar["invertDisabled"])

    def test_selection_bar_appears_while_a_row_is_selected(self) -> None:
        shown = self._render(selection=["Mods/SprocketLaserRangefinder.dll"])["toolbar"]
        self.assertFalse(shown["barHidden"])
        self.assertEqual(shown["selection"], "1 selected")
        self.assertFalse(shown["toggle"]["disabled"])
        self.assertFalse(shown["invertDisabled"])

    def test_toggle_selects_every_visible_row_then_clears_the_selection(self) -> None:
        every_row = [
            "Mods/SprocketLaserRangefinder.dll",
            "Mods/CannonSoundPoolFix.dll.disable",
            "UserLibs/UniverseLib.ML.IL2CPP.Interop.dll",
        ]
        all_selected = self._render(selection=every_row)["toolbar"]
        self.assertEqual(all_selected["toggle"]["text"], "Clear selection",
                         "整屏都选中时那一格变成「取消选择」")

        selected = self._render(action="toggle-selection")["toolbar"]
        self.assertEqual(selected["selection"], "3 selected")
        self.assertEqual(selected["toggle"]["text"], "Clear selection")

        cleared = self._render(selection=every_row, action="toggle-selection")["toolbar"]
        self.assertTrue(cleared["barHidden"], "取消选择后操作栏收起来")
        self.assertEqual(cleared["selection"], "")

    def test_invert_flips_the_visible_rows(self) -> None:
        one_of_three = self._render(
            selection=["Mods/SprocketLaserRangefinder.dll"],
            action="invert-selection",
        )["toolbar"]
        self.assertEqual(one_of_three["selection"], "2 selected", "三行里选中一行，反选后剩两行")

        every_row = [
            "Mods/SprocketLaserRangefinder.dll",
            "Mods/CannonSoundPoolFix.dll.disable",
            "UserLibs/UniverseLib.ML.IL2CPP.Interop.dll",
        ]
        none = self._render(selection=every_row, action="invert-selection")["toolbar"]
        self.assertEqual(none["selection"], "")
        self.assertTrue(none["barHidden"])

    def test_filter_chips_count_the_whole_list_and_mark_the_active_one(self) -> None:
        package = {"id": "furryaxw.sprocket-laser-rangefinder", "release": {"version": "0.2.0"}}
        filters = self._render(packages=[package])["toolbar"]["filters"]
        self.assertEqual(filters["all"], {"text": "All (3)", "active": True})
        self.assertEqual(filters["enabled"], {"text": "Enabled (2)", "active": False})
        self.assertEqual(filters["disabled"], {"text": "Disabled (1)", "active": False})
        self.assertEqual(filters["outdated"], {"text": "Updates (1)", "active": False})

    def test_filter_hides_rows_that_do_not_match(self) -> None:
        disabled = self._render(filter_key="disabled")
        self.assertEqual(len(disabled["rows"]), 1, "只留被禁用的那一行")
        self.assertIn("Cannon Sound Pool Fix", " | ".join(
            child["text"] for child in disabled["rows"][0]["children"][1]["children"]
        ))
        self.assertTrue(disabled["toolbar"]["filters"]["disabled"]["active"])
        self.assertEqual(disabled["count"], "3 detected mods", "标题仍是全集数量，口径计数在药丸上")

        outdated = self._render(
            packages=[{"id": "furryaxw.sprocket-laser-rangefinder", "release": {"version": "0.2.0"}}],
            filter_key="outdated",
        )
        self.assertEqual(len(outdated["rows"]), 1)
        self.assertIn("Version 0.2.0 available", " | ".join(
            child["text"] for child in outdated["rows"][0]["children"][2]["children"]
        ))

    def test_filter_without_matches_says_so_instead_of_pretending_nothing_is_installed(self) -> None:
        # 没有任何可更新的模组时切到「有更新」：空态要说「没有符合筛选」，不能说「没有检测到模组」。
        result = self._render(filter_key="outdated")
        self.assertEqual(len(result["rows"]), 1, "只有空态那一块")
        self.assertEqual(result["rows"][0]["className"], "empty-list")
        self.assertEqual(result["rows"][0]["children"][0]["text"], "No mods match this filter")
        self.assertEqual(result["count"], "3 detected mods", "空态也不该说磁盘上没有模组")
        self.assertEqual(len(result["toolbar"]["filters"]), 4)

    def test_double_clicking_a_catalog_row_opens_it_in_the_catalog(self) -> None:
        """双击那一行的去处是模组目录页：切过去并选中这个包。"""
        result = self._render(
            packages=[{"id": "furryaxw.sprocket-laser-rangefinder", "release": {"version": "0.1.3"}}],
            dblclick_rows=[0],
        )
        self.assertEqual(focus_calls(result), ["furryaxw.sprocket-laser-rangefinder"])
        self.assertEqual(open_location_calls(result), [], "目录里有这个包就不该去开资源管理器")

    def test_double_clicking_a_local_only_row_falls_back_to_the_file_location(self) -> None:
        """目录里查无此包（`UserLibs` 里的库）时，双击退到在资源管理器里定位文件。"""
        result = self._render(dblclick_rows=[2])
        self.assertEqual(
            open_location_calls(result),
            [["open_mod_location", "UserLibs/UniverseLib.ML.IL2CPP.Interop.dll"]],
        )
        self.assertEqual(focus_calls(result), [])

    def test_a_single_click_on_a_row_does_nothing(self) -> None:
        """单击不做事：跳页会丢当前视野，只有双击才走。"""
        result = self._render(
            packages=[{"id": "furryaxw.sprocket-laser-rangefinder", "release": {"version": "0.1.3"}}],
            click_rows=[0, 1],
        )
        self.assertEqual(focus_calls(result), [])
        self.assertEqual(open_location_calls(result), [])
        self.assertEqual(result["toolbar"]["selection"], "")

    def test_right_clicking_a_row_toggles_that_rows_selection(self) -> None:
        result = self._render(context_rows=[1])
        self.assertIn("selected", result["rows"][1]["className"])
        self.assertTrue(result["rows"][1]["children"][0]["checked"])
        self.assertNotIn("selected", result["rows"][0]["className"])
        self.assertEqual(result["toolbar"]["selection"], "1 selected")

    def test_double_clicking_a_control_inside_a_row_leaves_the_row_alone(self) -> None:
        """行内控件有自己的动作：既不选中这一行，也不跳转。"""
        result = self._render(
            packages=[{"id": "furryaxw.sprocket-laser-rangefinder", "release": {"version": "0.1.3"}}],
            dblclick_row_buttons=[0],
        )
        self.assertNotIn("selected", result["rows"][0]["className"])
        self.assertEqual(result["toolbar"]["selection"], "")
        self.assertEqual(focus_calls(result), [])
        self.assertEqual(open_location_calls(result), [])

    def test_batch_buttons_follow_what_the_selection_can_actually_do(self) -> None:
        package = {"id": "furryaxw.sprocket-laser-rangefinder", "release": {"version": "0.2.0"}}
        # 一个可更新、可禁用、可卸载的模组：三个按钮都该亮。
        one = self._render(packages=[package], selection=["Mods/SprocketLaserRangefinder.dll"])
        self.assertEqual(one["toolbar"]["selection"], "1 selected")
        self.assertEqual(
            one["toolbar"]["buttons"],
            {"update-selected": False, "disable-selected": False, "enable-selected": True, "remove-selected": False},
            "已启用的行只能再禁用；启用按钮没有活可干",
        )

        # `UserLibs` 的库既不能改名也没有归属：批量按钮都不该亮（工具栏只剩禁用的按钮）。
        library = self._render(selection=["UserLibs/UniverseLib.ML.IL2CPP.Interop.dll"])
        self.assertEqual(
            library["toolbar"]["buttons"],
            {"update-selected": True, "disable-selected": True, "enable-selected": True, "remove-selected": True},
        )

        # 被禁用的模组反过来只能启用。
        disabled = self._render(selection=["Mods/CannonSoundPoolFix.dll.disable"])
        self.assertEqual(disabled["toolbar"]["buttons"]["enable-selected"], False)
        self.assertEqual(disabled["toolbar"]["buttons"]["disable-selected"], True)

    def test_an_incompatible_update_is_an_exclamation_with_the_reason_in_its_tooltip(self) -> None:
        """行里只留一枚感叹号；「哪一轴拦下来的」整句走 tooltip，不占列表宽度。"""
        package = {
            "id": "furryaxw.sprocket-laser-rangefinder",
            "release": {"version": "0.2.0", "verdict": "incompatible"},
        }
        result = self._render(
            packages=[package],
            environment={
                "sprocket": {"version": "0.2.53.2"},
                "melonloader": {"used_version": "0.7.3"},
            },
        )

        marker = _find(result["rows"][0], lambda node: "update-alert" in node["className"])
        self.assertIsNotNone(marker, "被环境拦下来的新版本要挂一枚标记")
        self.assertEqual(marker["text"], "!", "行里只放感叹号，不摊开整句")
        reason = (
            "Update to 0.2.0 is available, but it does not support your "
            "Sprocket 0.2.53.2 and MelonLoader 0.7.3"
        )
        self.assertEqual(marker["title"], reason)
        self.assertEqual(marker["ariaLabel"], reason, "tooltip 之外还要有可读标签")

    def test_batch_update_enqueues_only_the_selected_outdated_packages(self) -> None:
        package = {"id": "furryaxw.sprocket-laser-rangefinder", "release": {"version": "0.2.0"}}
        result = self._render(
            packages=[package],
            selection=["Mods/SprocketLaserRangefinder.dll", "Mods/CannonSoundPoolFix.dll.disable"],
            action="update-selected",
        )
        queued = [entry["args"] for entry in result["apiCalls"] if entry["args"][0] == "enqueue_install"]
        self.assertEqual(
            queued,
            [["enqueue_install", ["furryaxw.sprocket-laser-rangefinder"], False, False,
              {"furryaxw.sprocket-laser-rangefinder": "0.2.0"}]],
            "只有选中且确实有新版的行才排队，并且装的就是行上写的那一版",
        )

    def test_a_blocked_update_does_not_count_as_an_update(self) -> None:
        """被兼容性拦下来的新版本不算「有更新」：按钮不点亮，「有更新」筛选也不数它。"""
        package = {
            "id": "furryaxw.sprocket-laser-rangefinder",
            "release": {"version": "0.2.0", "verdict": "incompatible"},
        }
        result = self._render(
            packages=[package],
            selection=["Mods/SprocketLaserRangefinder.dll"],
            action="update-selected",
        )

        self.assertTrue(result["toolbar"]["buttons"]["update-selected"], "拦下来就不该点亮「更新」")
        self.assertEqual(result["toolbar"]["filters"]["outdated"]["text"], "Updates (0)")
        queued = [entry["args"] for entry in result["apiCalls"] if entry["args"][0] == "enqueue_install"]
        self.assertEqual(queued, [], "红版不入队")
        self.assertEqual(
            [entry["kind"] for entry in result["apiCalls"] if entry["kind"] == "error"], [],
            "没有更新是正常情况，不该报错",
        )

    def test_a_runnable_update_under_an_incompatible_newest_still_offers_it(self) -> None:
        """最新那版跑不了、中间有能跑的：按钮点亮、装的是能跑的那版，另外留枚感叹号说明为什么不是最新。"""
        package = {
            "id": "furryaxw.sprocket-laser-rangefinder",
            "release": {"version": "0.3.0", "verdict": "incompatible"},
            "releases": [
                {"version": "0.3.0", "verdict": "incompatible"},
                {"version": "0.2.0", "verdict": "compatible"},
            ],
        }
        result = self._render(
            packages=[package],
            selection=["Mods/SprocketLaserRangefinder.dll"],
            action="update-selected",
        )

        self.assertFalse(result["toolbar"]["buttons"]["update-selected"], "有能装的那版就该点亮")
        row = result["rows"][0]
        self.assertIsNotNone(_find(row, lambda node: node["text"] == "Version 0.2.0 available"))
        self.assertIsNotNone(_find(row, lambda node: "update-alert" in node["className"]))
        queued = [entry["args"] for entry in result["apiCalls"] if entry["args"][0] == "enqueue_install"]
        self.assertEqual(
            queued,
            [["enqueue_install", ["furryaxw.sprocket-laser-rangefinder"], False, False,
              {"furryaxw.sprocket-laser-rangefinder": "0.2.0"}]],
        )

    def test_batch_toggle_calls_the_api_per_selected_path(self) -> None:
        result = self._render(
            selection=["Mods/SprocketLaserRangefinder.dll", "Mods/CannonSoundPoolFix.dll.disable"],
            action="disable-selected",
        )
        toggles = [entry["args"] for entry in result["apiCalls"] if entry["args"][0] == "toggle_mod"]
        self.assertEqual(
            toggles,
            [["toggle_mod", "Mods/SprocketLaserRangefinder.dll", False]],
            "已经是禁用状态的行不重复调用",
        )

    def test_batch_remove_confirms_once_and_removes_every_selected_package(self) -> None:
        result = self._render(
            selection=["Mods/SprocketLaserRangefinder.dll"],
            action="remove-selected",
        )
        removes = [entry["args"] for entry in result["apiCalls"] if entry["args"][0] == "remove"]
        self.assertEqual(removes, [["remove", "furryaxw.sprocket-laser-rangefinder"]])
        self.assertEqual([entry for entry in result["apiCalls"] if entry["kind"] == "error"], [])


class SelectionBarStyleTests(unittest.TestCase):
    """悬浮操作栏的样式契约：不占位（列表后不留空行）、按内容收窄、贴在页面底边。"""

    def setUp(self) -> None:
        self.css = (CLIENT_UI / "app.css").read_text(encoding="utf-8")

    def _rule(self, selector: str) -> str:
        # 行首匹配：`.selection-bar {` 也是 `.catalog-selection-anchor .selection-bar {` 的子串。
        match = re.compile(rf"^{re.escape(selector)} \{{", re.MULTILINE).search(self.css)
        self.assertIsNotNone(match, f"CSS 里没有 {selector} 规则")
        start = match.start()
        return self.css[start:self.css.index("}", start)]

    def test_the_anchor_occupies_no_space(self) -> None:
        rule = self._rule(".selection-anchor")
        self.assertIn("position: relative", rule)
        self.assertIn("height: 0", rule)

    def test_the_list_scrolls_instead_of_the_page(self) -> None:
        """栏贴的是页面底边：整页滚动时列表一短 `sticky` 就不生效，栏会跟着内容停在中间。"""
        page = self._rule("#page-installed.active")
        self.assertIn("overflow: hidden", page)
        self.assertIn("flex-direction: column", page)
        listing = self._rule("#installed-list")
        self.assertIn("overflow: auto", listing)
        self.assertIn("flex: 1", listing)
        self.assertIn("min-height: 0", listing)

    def test_the_bar_floats_and_shrinks_to_its_content(self) -> None:
        rule = self._rule(".selection-bar")
        self.assertIn("position: absolute", rule)
        self.assertIn("width: max-content", rule)
        self.assertIn("max-width: 100%", rule)

    def test_the_bar_is_centered_over_the_list(self) -> None:
        rule = self._rule(".selection-bar")
        self.assertIn("left: 0", rule)
        self.assertIn("right: 0", rule)
        self.assertIn("margin: 0 auto", rule)


if __name__ == "__main__":
    unittest.main()
