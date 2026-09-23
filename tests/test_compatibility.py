"""环境判定的语义：区间匹配、三色、加载器↔游戏自洽。"""

from __future__ import annotations

import unittest

from sprocket_mod_manager.domain.compatibility import (
    COMPATIBLE,
    INCOMPATIBLE,
    MELONLOADER_PACKAGE,
    SPROCKET_PACKAGE,
    UNKNOWN,
    Environment,
    environment_table,
    range_allows,
)


class RangeTests(unittest.TestCase):
    def test_four_segment_bounds_compare_numerically(self) -> None:
        self.assertTrue(range_allows("0.2.53.2", ">=0.2.53.1 <=0.2.53.2"))
        self.assertFalse(range_allows("0.2.53.3", ">=0.2.53.1 <=0.2.53.2"))
        self.assertTrue(range_allows("0.2.53.0", "<0.2.54.0"))
        self.assertFalse(range_allows("0.2.54.0", "<0.2.54.0"))

    def test_shorter_bounds_pad_with_zeros(self) -> None:
        self.assertTrue(range_allows("0.2.53.2", "<0.2.54"), "写短的比较符上界")
        self.assertFalse(range_allows("0.2.54.0", "<0.2.54"))
        self.assertFalse(range_allows("0.2.2.0", ">=0.2.10"), "按段比较，不是按字符串")

    def test_branches_and_wildcards(self) -> None:
        self.assertTrue(range_allows("0.2.60.1", ">=0.2.50 <0.2.54 || >=0.2.60 <0.2.61"))
        self.assertFalse(range_allows("0.2.55.0", ">=0.2.50 <0.2.54 || >=0.2.60 <0.2.61"))
        self.assertTrue(range_allows("0.7.3", "0.7.x"))
        self.assertFalse(range_allows("0.8.0", "0.7.x"))
        self.assertTrue(range_allows("0.7.3", "*"))

    def test_unparsable_input_is_not_a_match(self) -> None:
        self.assertFalse(range_allows("", ">=0.7.0"))
        self.assertFalse(range_allows("0.7.3", "^0.7.3"), "规范形式里没有 caret，不猜")
        self.assertFalse(range_allows("0.7.3", ">=nonsense"))


class EnvironmentTableTests(unittest.TestCase):
    def test_a_valid_table_is_kept(self) -> None:
        table = environment_table(
            {"schema_version": 1, "entries": [{"melonloader": ">=0.7.0", "sprocket": "<0.3.0.0"}]}
        )

        self.assertEqual(
            table["entries"], [{"melonloader": ">=0.7.0", "sprocket": "<0.3.0.0"}]
        )

    def test_a_missing_or_broken_table_is_empty(self) -> None:
        # 本地不放内置表：没有注册表那份就是「不知道」，不猜平台事实。
        for payload in (None, {}, {"entries": []}, {"entries": ["nope"]}, {"entries": [{"melonloader": "x"}]}):
            with self.subTest(payload=payload):
                self.assertEqual(environment_table(payload), {"schema_version": 1, "entries": []})


class VerdictTests(unittest.TestCase):
    def environment(self, **overrides) -> Environment:
        values = {"sprocket": "0.2.53.2", "sprocket_state": "ok", "melonloader": "0.7.3"}
        values.update(overrides)
        return Environment(**values)

    def test_no_environment_dependency_is_unknown(self) -> None:
        self.assertEqual(self.environment().verdict([]), UNKNOWN)
        self.assertEqual(
            self.environment().verdict([{"id": "furryaxw.sprocket-depth", "version": ">=0.1.0"}]),
            UNKNOWN,
            "模组之间的依赖不参与环境判定",
        )

    def test_both_axes_have_to_pass(self) -> None:
        environment = self.environment()
        inside = [
            {"id": SPROCKET_PACKAGE, "version": ">=0.2.53.1 <=0.2.53.2"},
            {"id": MELONLOADER_PACKAGE, "version": ">=0.7.2 <0.8.0"},
        ]
        self.assertEqual(environment.verdict(inside), COMPATIBLE)

        outside = [
            {"id": SPROCKET_PACKAGE, "version": ">=0.2.53.1 <=0.2.53.2"},
            {"id": MELONLOADER_PACKAGE, "version": ">=0.6.0 <0.7.0"},
        ]
        self.assertEqual(environment.verdict(outside), INCOMPATIBLE)

    def test_an_unknown_axis_does_not_decide_anything(self) -> None:
        environment = self.environment(melonloader=None)
        self.assertEqual(
            environment.verdict([{"id": MELONLOADER_PACKAGE, "version": "<0.6.0"}]),
            UNKNOWN,
        )
        self.assertEqual(
            environment.verdict([{"id": SPROCKET_PACKAGE, "version": "<0.2.54"}]),
            COMPATIBLE,
            "另一轴还是能判定",
        )

    def test_an_unusable_game_version_makes_everything_incompatible(self) -> None:
        for state in ("legacy", "unreadable"):
            with self.subTest(state=state):
                environment = self.environment(sprocket=None, sprocket_state=state)
                self.assertEqual(environment.verdict([]), INCOMPATIBLE)
                self.assertEqual(
                    environment.verdict([{"id": SPROCKET_PACKAGE, "version": "*"}]),
                    INCOMPATIBLE,
                )

    def test_an_unconfigured_game_path_decides_nothing(self) -> None:
        environment = self.environment(sprocket=None, sprocket_state="unconfigured")
        self.assertEqual(environment.verdict([]), UNKNOWN)


class AxisDetailTests(unittest.TestCase):
    """逐轴结果：声明、本机值、过没过（详情页直接显示这三样）。"""

    def environment(self, **overrides) -> Environment:
        values = {"sprocket": "0.2.53.2", "sprocket_state": "ok", "melonloader": "0.7.3"}
        values.update(overrides)
        return Environment(**values)

    def axes(self, dependencies):
        return {axis["id"]: axis for axis in self.environment().axes(dependencies)}

    def test_both_axes_are_reported_even_when_nothing_is_declared(self) -> None:
        axes = self.axes([])

        self.assertEqual(set(axes), {SPROCKET_PACKAGE, MELONLOADER_PACKAGE})
        for axis in axes.values():
            self.assertEqual(axis["declared"], "")
            self.assertIsNone(axis["satisfied"], "没声明就不参与判定")

    def test_a_declared_axis_reports_the_local_value_and_whether_it_passed(self) -> None:
        axes = self.axes([
            {"id": SPROCKET_PACKAGE, "version": ">=0.2.53.0 <0.2.54.0"},
            {"id": MELONLOADER_PACKAGE, "version": ">=0.8.0"},
        ])

        self.assertEqual(axes[SPROCKET_PACKAGE]["local"], "0.2.53.2")
        self.assertIs(axes[SPROCKET_PACKAGE]["satisfied"], True)
        self.assertEqual(axes[MELONLOADER_PACKAGE]["declared"], ">=0.8.0")
        self.assertIs(axes[MELONLOADER_PACKAGE]["satisfied"], False)

    def test_an_unknown_local_version_keeps_the_axis_out_of_the_verdict(self) -> None:
        environment = self.environment(melonloader=None)

        axes = {axis["id"]: axis for axis in environment.axes(
            [{"id": MELONLOADER_PACKAGE, "version": "<0.7.0"}]
        )}

        self.assertEqual(axes[MELONLOADER_PACKAGE]["declared"], "<0.7.0")
        self.assertEqual(axes[MELONLOADER_PACKAGE]["local"], "")
        self.assertIsNone(axes[MELONLOADER_PACKAGE]["satisfied"], "本机值未知就不能说它没过")

    def test_an_unusable_game_version_keeps_the_axis_out_of_the_verdict(self) -> None:
        environment = self.environment(sprocket=None, sprocket_state="legacy")

        axes = {axis["id"]: axis for axis in environment.axes(
            [{"id": SPROCKET_PACKAGE, "version": "0.2.53.x"}]
        )}

        self.assertIsNone(axes[SPROCKET_PACKAGE]["satisfied"])


class ConsistencyTests(unittest.TestCase):
    TABLE = {"entries": [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}]}

    def environment(self, sprocket: str, melonloader: str | None, table: dict | None = None) -> Environment:
        return Environment(
            sprocket=sprocket,
            sprocket_state="ok",
            melonloader=melonloader,
            table=self.TABLE if table is None else table,
        )

    def test_a_loader_that_supports_the_game_is_consistent(self) -> None:
        self.assertEqual(self.environment("0.2.53.2", "0.7.3").consistency()["state"], "ok")

    def test_a_game_newer_than_the_loader_supports_is_a_conflict(self) -> None:
        state = self.environment("0.2.54.2", "0.7.3").consistency()
        self.assertEqual(state["state"], "conflict")
        self.assertEqual(state["entry"]["sprocket"], "<0.2.54.0")

    def test_a_loader_outside_the_table_is_unknown(self) -> None:
        self.assertEqual(self.environment("0.2.54.2", "0.6.1").consistency()["state"], "unknown")

    def test_without_a_loader_version_nothing_is_claimed(self) -> None:
        self.assertEqual(self.environment("0.2.54.2", None).consistency()["state"], "unknown")

    def test_without_a_table_nothing_is_claimed(self) -> None:
        # 一次都没同步到表（新装、离线）：这层就是未知，不按"兼容"处理。
        environment = self.environment("0.2.54.2", "0.7.3", table={})
        self.assertEqual(environment.consistency()["state"], "unknown")
        self.assertIsNone(environment.loader_row())

    def test_the_table_is_used_as_given(self) -> None:
        environment = self.environment(
            "0.2.54.2", "0.7.3", table={"entries": [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.3.0.0"}]}
        )
        self.assertEqual(environment.consistency()["state"], "ok")

    def test_the_payload_carries_the_table_source(self) -> None:
        environment = self.environment("0.2.53.2", "0.7.3")
        self.assertEqual(
            environment.as_dict(table_source="registry")["table_source"], "registry"
        )


class OverlappingRowsTests(unittest.TestCase):
    """加载器追上游戏之后新增一行：老那一行不能被它带坏，新加载器也不能被老行拦住。"""

    TABLE = {
        "entries": [
            {"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"},
            {"melonloader": ">=0.7.4 <0.8.0", "sprocket": "*"},
        ]
    }

    def environment(self, sprocket: str, melonloader: str) -> Environment:
        return Environment(
            sprocket=sprocket, sprocket_state="ok", melonloader=melonloader, table=self.TABLE
        )

    def test_the_newer_row_wins_for_the_loader_it_covers(self) -> None:
        state = self.environment("0.2.54.2", "0.7.4").consistency()

        self.assertEqual(state["state"], "ok")
        self.assertIsNone(state["entry"])

    def test_the_older_loader_still_only_matches_its_own_row(self) -> None:
        state = self.environment("0.2.54.2", "0.7.3").consistency()

        self.assertEqual(state["state"], "conflict")
        self.assertEqual(state["entry"]["sprocket"], "<0.2.54.0")

    def test_the_newer_row_keeps_supporting_older_games(self) -> None:
        self.assertEqual(self.environment("0.2.53.2", "0.7.4").consistency()["state"], "ok")

    def test_a_row_without_a_lower_bound_is_the_last_resort(self) -> None:
        environment = Environment(
            sprocket="0.2.54.2",
            sprocket_state="ok",
            melonloader="0.7.3",
            table={
                "entries": [
                    {"melonloader": "*", "sprocket": "<0.2.54.0"},
                    {"melonloader": ">=0.7.0 <0.8.0", "sprocket": "*"},
                ]
            },
        )

        self.assertEqual(environment.consistency()["state"], "ok", "下界更大的一行优先")

    def test_the_last_row_wins_when_the_lower_bounds_match(self) -> None:
        environment = Environment(
            sprocket="0.2.54.2",
            sprocket_state="ok",
            melonloader="0.7.3",
            table={
                "entries": [
                    {"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"},
                    {"melonloader": ">=0.7.0 <0.8.0", "sprocket": "*"},
                ]
            },
        )

        self.assertEqual(environment.consistency()["state"], "ok")

    def test_loader_row_reports_the_one_that_applies(self) -> None:
        self.assertEqual(
            self.environment("0.2.53.2", "0.7.4").loader_row(),
            {"melonloader": ">=0.7.4 <0.8.0", "sprocket": "*"},
        )
        self.assertIsNone(self.environment("0.2.53.2", "0.6.1").loader_row())


if __name__ == "__main__":
    unittest.main()
