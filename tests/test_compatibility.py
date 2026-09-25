"""能力判定的语义：区间匹配、三色、加载器包 ↔ 游戏自洽。"""

from __future__ import annotations

import unittest

from sprocket_mod_manager.domain.compatibility import (
    COMPATIBLE,
    INCOMPATIBLE,
    UNKNOWN,
    CapabilityEnvironment,
    loader_table_decision,
    providers_table,
    range_allows,
    release_verdict,
)
from sprocket_mod_manager.domain.models import RegistryPackage

LOADER_ID = "lavagang.melonloader"
GAME = "hamish.sprocket"


def _bridge_package() -> RegistryPackage:
    """桥接加载器：自己的包版本与它供给的 melonloader 能力版本是两回事。"""
    return RegistryPackage.from_dict(
        {
            "schema_version": 2,
            "id": "1499501762.bepinex-melonloader-loader",
            "name": "MLLoader",
            "authors": ["1499501762"],
            "repository": "1499501762/BepInEx.MelonLoader.Loader",
            "license": "Apache-2.0",
            "display_name": {"en": "MLLoader"},
            "release": {
                "include_prerelease": False,
                "version_pattern": r"^v?(\d+\.\d+\.\d+)$",
                "assets": {"include": ["*.zip"], "exclude": []},
            },
            "dependencies": [],
            "install": {"payload": [{"match": "**", "target": "{Sprocket}", "layout": "tree"}], "exclude": []},
            "category": "utility",
            "tags": [],
            "kind": "loaderbridge",
            "supply": {"melonloader:mod": "{Sprocket}/MLLoader/Mods"},
            "provides": {LOADER_ID: "0.7.3"},
        }
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


class ProvidersTableTests(unittest.TestCase):
    def test_a_valid_table_is_kept(self) -> None:
        table = providers_table(
            {
                "schema_version": 2,
                "entries": [{"loader": LOADER_ID, "version": ">=0.7.0", "sprocket": "<0.3.0.0"}],
            }
        )

        self.assertEqual(
            table["entries"],
            [{"loader": LOADER_ID, "version": ">=0.7.0", "sprocket": "<0.3.0.0"}],
        )

    def test_a_missing_or_broken_table_is_empty(self) -> None:
        # 本地不放内置表：没有注册表那份就是「不知道」，不猜平台事实。
        for payload in (None, {}, {"entries": []}, {"entries": ["nope"]}, {"entries": [{"loader": LOADER_ID}]}):
            with self.subTest(payload=payload):
                self.assertEqual(providers_table(payload), {"schema_version": 2, "entries": []})


class VerdictTests(unittest.TestCase):
    def environment(self, **overrides) -> CapabilityEnvironment:
        values = {
            "game_id": GAME,
            "sprocket": "0.2.53.2",
            "sprocket_state": "ok",
            "capabilities": {LOADER_ID: "0.7.3"},
            "loaders": {LOADER_ID: "0.7.3"},
        }
        values.update(overrides)
        return CapabilityEnvironment(**values)

    def test_no_capability_dependency_is_unknown(self) -> None:
        self.assertEqual(self.environment().verdict([]), UNKNOWN)
        self.assertEqual(
            self.environment().verdict([{"id": "furryaxw.sprocket-depth", "version": ">=0.1.0"}]),
            UNKNOWN,
            "模组之间的依赖不参与能力判定",
        )

    def test_both_capabilities_have_to_pass(self) -> None:
        environment = self.environment()
        inside = [
            {"id": GAME, "version": ">=0.2.53.1 <=0.2.53.2"},
            {"id": LOADER_ID, "version": ">=0.7.2 <0.8.0"},
        ]
        self.assertEqual(environment.verdict(inside), COMPATIBLE)

        outside = [
            {"id": GAME, "version": ">=0.2.53.1 <=0.2.53.2"},
            {"id": LOADER_ID, "version": ">=0.6.0 <0.7.0"},
        ]
        self.assertEqual(environment.verdict(outside), INCOMPATIBLE)

    def test_a_capability_nobody_supplies_decides_nothing(self) -> None:
        environment = self.environment(capabilities={}, loaders={})
        self.assertEqual(
            environment.verdict([{"id": LOADER_ID, "version": "<0.6.0"}]),
            UNKNOWN,
        )
        self.assertEqual(
            environment.verdict([{"id": GAME, "version": "<0.2.54"}]),
            COMPATIBLE,
            "另一根能力轴还是能判定",
        )

    def test_an_unusable_game_version_makes_everything_incompatible(self) -> None:
        for state in ("legacy", "unreadable"):
            with self.subTest(state=state):
                environment = self.environment(sprocket=None, sprocket_state=state)
                self.assertEqual(environment.verdict([]), INCOMPATIBLE)
                self.assertEqual(
                    environment.verdict([{"id": GAME, "version": "*"}]),
                    INCOMPATIBLE,
                )

    def test_an_unconfigured_game_path_decides_nothing(self) -> None:
        environment = self.environment(sprocket=None, sprocket_state="unconfigured")
        self.assertEqual(environment.verdict([]), UNKNOWN)


class AxisDetailTests(unittest.TestCase):
    """逐轴结果：声明、本机值、过没过（详情页直接显示这三样）。"""

    def environment(self, **overrides) -> CapabilityEnvironment:
        values = {
            "game_id": GAME,
            "sprocket": "0.2.53.2",
            "sprocket_state": "ok",
            "capabilities": {LOADER_ID: "0.7.3"},
            "loaders": {LOADER_ID: "0.7.3"},
        }
        values.update(overrides)
        return CapabilityEnvironment(**values)

    def axes(self, dependencies):
        return {axis["id"]: axis for axis in self.environment().axes(dependencies)}

    def test_both_capabilities_are_reported_even_when_nothing_is_declared(self) -> None:
        axes = self.axes([])

        self.assertEqual(set(axes), {GAME, LOADER_ID})
        for axis in axes.values():
            self.assertEqual(axis["declared"], "")
            self.assertIsNone(axis["satisfied"], "没声明就不参与判定")

    def test_a_declared_capability_reports_the_local_value_and_whether_it_passed(self) -> None:
        axes = self.axes([
            {"id": GAME, "version": ">=0.2.53.0 <0.2.54.0"},
            {"id": LOADER_ID, "version": ">=0.8.0"},
        ])

        self.assertEqual(axes[GAME]["local"], "0.2.53.2")
        self.assertIs(axes[GAME]["satisfied"], True)
        self.assertEqual(axes[LOADER_ID]["declared"], ">=0.8.0")
        self.assertIs(axes[LOADER_ID]["satisfied"], False)

    def test_an_unsupplied_capability_keeps_the_axis_out_of_the_verdict(self) -> None:
        environment = self.environment(capabilities={}, loaders={})

        axes = {axis["id"]: axis for axis in environment.axes(
            [{"id": LOADER_ID, "version": "<0.7.0"}]
        )}

        self.assertEqual(axes[LOADER_ID]["declared"], "<0.7.0")
        self.assertEqual(axes[LOADER_ID]["local"], "")
        self.assertIsNone(axes[LOADER_ID]["satisfied"], "本机值未知就不能说它没过")

    def test_an_unusable_game_version_keeps_the_axis_out_of_the_verdict(self) -> None:
        environment = self.environment(sprocket=None, sprocket_state="legacy")

        axes = {axis["id"]: axis for axis in environment.axes(
            [{"id": GAME, "version": "0.2.53.x"}]
        )}

        self.assertIsNone(axes[GAME]["satisfied"])


class ConsistencyTests(unittest.TestCase):
    TABLE = {"entries": [{"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}]}

    def environment(self, sprocket: str, loader: str | None, table: dict | None = None) -> CapabilityEnvironment:
        return CapabilityEnvironment(
            game_id=GAME,
            sprocket=sprocket,
            sprocket_state="ok",
            loaders={} if loader is None else {LOADER_ID: loader},
            table=self.TABLE if table is None else table,
        )

    def test_a_loader_that_supports_the_game_is_consistent(self) -> None:
        self.assertEqual(self.environment("0.2.53.2", "0.7.3").consistency()["state"], "ok")

    def test_a_game_newer_than_the_loader_supports_is_a_conflict(self) -> None:
        state = self.environment("0.2.54.2", "0.7.3").consistency()
        self.assertEqual(state["state"], "conflict")
        self.assertEqual(state["entry"]["sprocket"], "<0.2.54.0")
        self.assertEqual(state["loader"], LOADER_ID)

    def test_a_loader_outside_the_table_claims_nothing(self) -> None:
        # 表里没有一行覆盖这个加载器版本：这层不知道，也不按「兼容」处理。
        environment = self.environment("0.2.54.2", "0.6.1")
        self.assertEqual(environment.consistency()["state"], "ok")
        self.assertEqual(environment.consistency()["entry"], None)
        self.assertIsNone(environment.loader_row(LOADER_ID))

    def test_without_a_loader_version_nothing_is_claimed(self) -> None:
        self.assertEqual(self.environment("0.2.54.2", None).consistency()["state"], "unknown")

    def test_without_a_table_nothing_is_claimed(self) -> None:
        environment = self.environment("0.2.54.2", "0.7.3", table={})
        self.assertEqual(environment.consistency()["state"], "ok")
        self.assertIsNone(environment.consistency()["entry"])
        self.assertIsNone(environment.loader_row(LOADER_ID))

    def test_the_table_is_used_as_given(self) -> None:
        environment = self.environment(
            "0.2.54.2",
            "0.7.3",
            table={"entries": [{"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": "<0.3.0.0"}]},
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
            {"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"},
            {"loader": LOADER_ID, "version": ">=0.7.4 <0.8.0", "sprocket": "*"},
        ]
    }

    def environment(self, sprocket: str, loader: str) -> CapabilityEnvironment:
        return CapabilityEnvironment(
            game_id=GAME,
            sprocket=sprocket,
            sprocket_state="ok",
            loaders={LOADER_ID: loader},
            table=self.TABLE,
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
        environment = CapabilityEnvironment(
            game_id=GAME,
            sprocket="0.2.54.2",
            sprocket_state="ok",
            loaders={LOADER_ID: "0.7.3"},
            table={
                "entries": [
                    {"loader": LOADER_ID, "version": "*", "sprocket": "<0.2.54.0"},
                    {"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": "*"},
                ]
            },
        )

        self.assertEqual(environment.consistency()["state"], "ok", "下界更大的一行优先")

    def test_the_last_row_wins_when_the_lower_bounds_match(self) -> None:
        environment = CapabilityEnvironment(
            game_id=GAME,
            sprocket="0.2.54.2",
            sprocket_state="ok",
            loaders={LOADER_ID: "0.7.3"},
            table={
                "entries": [
                    {"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"},
                    {"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": "*"},
                ]
            },
        )

        self.assertEqual(environment.consistency()["state"], "ok")

    def test_loader_row_reports_the_one_that_applies(self) -> None:
        self.assertEqual(
            self.environment("0.2.53.2", "0.7.4").loader_row(LOADER_ID),
            {"loader": LOADER_ID, "version": ">=0.7.4 <0.8.0", "sprocket": "*"},
        )
        self.assertIsNone(self.environment("0.2.53.2", "0.6.1").loader_row(LOADER_ID))


class BridgeCapabilityTests(unittest.TestCase):
    """一个包供给的能力可以与它自己的包 id 不同：桥接加载器就是这样供给 melonloader 的。"""

    def test_a_bridge_supplies_the_capability_it_declares(self) -> None:
        bridge = _bridge_package()

        self.assertEqual(bridge.capabilities(), {LOADER_ID: "0.7.3"})
        self.assertNotIn(bridge.id, bridge.capabilities())

    def test_a_mod_declaring_that_capability_is_compatible(self) -> None:
        bridge = _bridge_package()
        environment = CapabilityEnvironment(
            game_id=GAME,
            sprocket="0.2.53.2",
            sprocket_state="ok",
            capabilities=bridge.capabilities(),
        )

        self.assertEqual(
            release_verdict(
                environment,
                category="utility",
                dependencies=({"id": LOADER_ID, "version": ">=0.7.0"},),
            ),
            COMPATIBLE,
        )

    def test_the_declared_fixed_version_is_used_as_is(self) -> None:
        bridge = _bridge_package()
        environment = CapabilityEnvironment(
            game_id=GAME,
            sprocket="0.2.53.2",
            sprocket_state="ok",
            capabilities=bridge.capabilities(),
        )

        self.assertEqual(
            release_verdict(
                environment,
                category="utility",
                dependencies=({"id": LOADER_ID, "version": ">=0.8.0"},),
            ),
            INCOMPATIBLE,
        )

    def test_the_bridge_provider_row_makes_the_capability_fit_the_newer_game(self) -> None:
        bridge = _bridge_package()
        environment = CapabilityEnvironment(
            game_id=GAME,
            sprocket="0.2.54.2",
            sprocket_state="ok",
            capabilities=bridge.capabilities(),
            loaders={bridge.id: "2.3.9"},
            table={
                "entries": [
                    {"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"},
                    {"loader": bridge.id, "version": ">=2.3.0", "sprocket": ">=0.2.54.0"},
                ]
            },
        )

        self.assertEqual(environment.consistency()["state"], "ok")
        self.assertEqual(
            release_verdict(
                environment,
                category="utility",
                dependencies=({"id": LOADER_ID, "version": ">=0.7.0"},),
            ),
            COMPATIBLE,
        )

    def test_a_prerelease_provider_row_matches_its_installed_build(self) -> None:
        environment = CapabilityEnvironment(
            game_id=GAME,
            sprocket="0.2.54.2",
            sprocket_state="ok",
            loaders={"bepinex.bepinex-be": "6.0.0-be.788"},
            table={
                "entries": [
                    {
                        "loader": "bepinex.bepinex-be",
                        "version": ">=6.0.0-be.785",
                        "sprocket": ">=0.2.54.0",
                    }
                ]
            },
        )

        self.assertEqual(environment.consistency()["state"], "ok")
        self.assertEqual(
            environment.loader_row("bepinex.bepinex-be")["version"], ">=6.0.0-be.785"
        )

    def test_a_loader_package_without_a_row_claims_nothing(self) -> None:
        environment = CapabilityEnvironment(
            game_id=GAME,
            sprocket="0.2.54.2",
            sprocket_state="ok",
            loaders={"test.unlisted-loader": "6.0.0-pre.2"},
            table={"entries": [{"loader": LOADER_ID, "version": ">=0.7.0", "sprocket": "<0.2.54.0"}]},
        )

        self.assertEqual(environment.consistency()["state"], "ok")
        self.assertIsNone(environment.loader_row("test.unlisted-loader"))


class LoaderTableDecisionTests(unittest.TestCase):
    """加载器发布的判定：按表里覆盖本机游戏版本的那一行，不看加载器自己不写的声明。"""

    TABLE = {
        "entries": [
            {"loader": LOADER_ID, "version": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"},
            {"loader": "bepinex.bepinex-be", "version": ">=6.0.0-be.785", "sprocket": ">=0.2.54.0"},
        ]
    }

    def test_the_row_covering_the_game_line_decides_and_declares_the_game_axis(self) -> None:
        verdict, dependencies = loader_table_decision(
            self.TABLE, LOADER_ID, GAME, "0.2.53.2", "0.7.3"
        )

        self.assertEqual(verdict, COMPATIBLE)
        self.assertEqual(dependencies[0]["id"], GAME, "声明就是那条游戏轴")
        self.assertEqual(dependencies[0]["version"], "<0.2.54.0")

    def test_a_release_outside_the_row_version_range_is_incompatible(self) -> None:
        verdict, dependencies = loader_table_decision(
            self.TABLE, LOADER_ID, GAME, "0.2.53.2", "0.8.1"
        )

        self.assertEqual(verdict, INCOMPATIBLE)
        self.assertEqual(dependencies, ())

    def test_a_loader_whose_rows_all_target_another_game_line_is_incompatible(self) -> None:
        verdict, _dependencies = loader_table_decision(
            self.TABLE, "bepinex.bepinex-be", GAME, "0.2.53.2", "6.0.0-be.788"
        )

        self.assertEqual(verdict, INCOMPATIBLE)

    def test_a_prerelease_version_inside_a_bepinex_row_is_compatible(self) -> None:
        verdict, _dependencies = loader_table_decision(
            self.TABLE, "bepinex.bepinex-be", GAME, "0.2.54.2", "6.0.0-be.788"
        )

        self.assertEqual(verdict, COMPATIBLE)

    def test_a_package_outside_the_table_keeps_its_own_declaration(self) -> None:
        self.assertIsNone(
            loader_table_decision(self.TABLE, "some.patch", GAME, "0.2.53.2", "1.0.0")
        )
        self.assertIsNone(
            loader_table_decision(self.TABLE, LOADER_ID, GAME, None, "0.7.3"),
            "游戏版本读不出来时这层不知道答案",
        )


if __name__ == "__main__":
    unittest.main()
