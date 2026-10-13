"""安装计划：动作、版本、红行原因、连带卸载，以及「永不拒绝」。

用一批合成条目走真实的求解与判定：版本与兼容性都只有后端一处口径，这份计划是它的形状。
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_solver_environment import SolverHarness, environment as env  # noqa: E402
from test_solver_environment import (  # noqa: E402
    package as env_package,
    release as env_release,
)

from sprocket_mod_manager.application.planning import (  # noqa: E402
    ACTION_INSTALL,
    ACTION_REMOVE,
    ACTION_SKIP,
    ChangePlanner,
    ChangeRequest,
)

API = "test.api"
HOST = "test.host"          # 要 api 1.x
CONFLICT = "test.conflict"  # 只有一个版本，要 api 0.3.x
LOOSE = "test.loose"        # 依赖 HOST，但有一个不依赖它的旧版本


def world():
    """api 有两个版本；host 要 1.x；conflict 只能要 0.3.x；loose 依赖 host。"""
    return SolverHarness([
        env_package("test.api", [env_release("1.0.0"), env_release("0.3.0")]),
        env_package(
            "test.host",
            [env_release("1.0.0")],
            depends_on=({"id": API, "version": ">=1.0.0", "when": "*"},),
        ),
        env_package(
            "test.conflict",
            [env_release("1.0.0")],
            depends_on=({"id": API, "version": ">=0.3.0 <1.0.0", "when": "*"},),
        ),
        env_package(
            "test.loose",
            [env_release("1.0.0"), env_release("0.9.0")],
            depends_on=({"id": "test.host", "version": "*", "when": ">=1.0.0"},),
        ),
    ])


def planner(harness: SolverHarness, state=None) -> ChangePlanner:
    return ChangePlanner(
        registry=harness.registry,
        github=harness.github(),
        environment=env(),
        state=state or {},
    )


def line_for(result, package_id):
    return next(item for item in result["lines"] if item["id"] == package_id)


class ChangePlanTests(unittest.TestCase):
    def test_a_plan_lists_every_package_it_would_install(self) -> None:
        result = planner(world()).plan(ChangeRequest(("test.host",)))

        self.assertFalse(result["no_solution"])
        self.assertEqual(
            [item["id"] for item in result["lines"]],
            ["test.host", "test.api"],
            "点名的排在前面，它带进来的依赖跟在后面 —— 每张卡片一行",
        )
        line = line_for(result, "test.host")
        self.assertEqual(line["action"], ACTION_INSTALL)
        self.assertEqual(line["version"], "1.0.0")
        self.assertTrue(line["named"], "这一行是用户点名的")
        self.assertFalse(line_for(result, "test.api")["named"], "跟着进来的依赖不是点名的那一行")
        self.assertEqual(
            result["install"],
            {"test.host": "1.0.0", "test.api": "1.0.0"},
            "会装的那些：点名的包加上它带进来的依赖",
        )
        self.assertEqual(line["problems"], [], "依赖会被一起解出来，没有缺口")
        self.assertEqual(line["depends_on"], ["test.api"], "这一行依赖谁")
        self.assertEqual(line_for(result, "test.api")["required_by"], ["test.host"], "谁依赖这一行")

    def test_the_plan_offers_the_recommendations_of_what_it_installs(self) -> None:
        """推荐来自计划里的每个包（依赖也算），只列现在真装得上的那些。"""
        harness = world()
        packages = [
            replace(item, recommendations=("test.extra",)) if item.id == API else item
            for item in harness.registry.index_packages
        ]
        packages.append(env_package("test.extra", [env_release("1.0.0")]))
        result = planner(SolverHarness(packages)).plan(ChangeRequest((HOST,)))

        self.assertEqual(
            result["recommendations"],
            [{
                "id": "test.extra",
                "name": "test.extra",
                "display_name": {"en": "test.extra"},
                "version": "1.0.0",
                "recommended_by": [API],
            }],
            "推荐自带「会装哪一版」与「谁推荐的」",
        )

    def test_a_recommendation_that_is_already_current_is_not_offered(self) -> None:
        harness = world()
        packages = [
            replace(item, recommendations=("test.extra",)) if item.id == API else item
            for item in harness.registry.index_packages
        ]
        packages.append(env_package("test.extra", [env_release("1.0.0")]))
        result = planner(
            SolverHarness(packages), state={"test.extra": {"version": "1.0.0"}}
        ).plan(ChangeRequest((HOST,)))

        self.assertEqual(result["recommendations"], [], "装着的就是那一版：没有可推荐的")

    def test_a_recommendation_already_in_the_plan_is_not_repeated(self) -> None:
        harness = world()
        packages = [
            replace(item, recommendations=("test.host",)) if item.id == API else item
            for item in harness.registry.index_packages
        ]
        result = planner(SolverHarness(packages)).plan(ChangeRequest((HOST,)))

        self.assertEqual(result["recommendations"], [], "已经在这一批里了，不重复列一次")

    def test_an_installed_dependency_that_needs_no_change_is_not_a_row(self) -> None:
        result = planner(world(), state={API: {"version": "1.0.0"}}).plan(
            ChangeRequest(("test.host",))
        )

        self.assertEqual([item["id"] for item in result["lines"]], ["test.host"], "它没有要落的盘")
        self.assertEqual(result["install"], {"test.host": "1.0.0"})
        self.assertEqual(
            line_for(result, "test.host")["depends_on"],
            ["test.api"],
            "不画框不等于不提它：依赖关系照旧写在这一行里",
        )

    def test_a_mutual_conflict_marks_both_lines_and_installs_nothing(self) -> None:
        result = planner(world()).plan(ChangeRequest(("test.host", "test.conflict")))

        self.assertFalse(result["no_solution"], "冲突不是拒绝：页面照开")
        self.assertEqual(result["install"], {}, "两边都不装，于是是空安装列表")
        for package_id in ("test.host", "test.conflict"):
            line = line_for(result, package_id)
            self.assertEqual(line["action"], ACTION_SKIP)
            self.assertEqual(line["problems"][0]["kind"], "conflict")

    def test_a_selected_package_without_a_dependency_gap_is_still_installed(self) -> None:
        result = planner(world()).plan(
            ChangeRequest(("test.host", "test.conflict", "test.api"))
        )

        self.assertEqual(result["install"], {"test.api": "1.0.0"})
        self.assertEqual(line_for(result, "test.api")["action"], ACTION_INSTALL)
        self.assertEqual(line_for(result, "test.host")["action"], ACTION_SKIP)
        self.assertEqual(line_for(result, "test.conflict")["action"], ACTION_SKIP)

    def test_skipping_a_dependency_marks_the_dependent_red(self) -> None:
        result = planner(world()).plan(
            ChangeRequest(("test.host", "test.api"), choices={"test.api": "skip"})
        )

        line = line_for(result, "test.host")
        self.assertEqual(line["action"], ACTION_INSTALL, "上游照装，缺口标红")
        self.assertEqual([item["kind"] for item in line["problems"]], ["blocked-by-skip"])
        self.assertEqual(line["problems"][0]["text"], "test.api >=1.0.0")
        self.assertEqual(line["ignored"], ["test.api"], "这个缺口执行时照旧留着")

    def test_a_named_version_that_cannot_coexist_has_no_solution(self) -> None:
        result = planner(world()).plan(
            ChangeRequest(("test.host", "test.conflict"), choices={"test.conflict": "1.0.0"})
        )

        self.assertTrue(result["no_solution"])
        self.assertEqual(result["lines"], [], "交给调用方保留上一个有效解")

    def test_the_versions_array_carries_every_candidate_with_its_verdict(self) -> None:
        result = planner(world()).plan(ChangeRequest(("test.host",)))

        versions = line_for(result, "test.host")["versions"]
        self.assertEqual([item["version"] for item in versions], ["1.0.0"])
        self.assertEqual(versions[0]["verdict"], "unknown", "没写能力声明就是未知")
        self.assertEqual(versions[0]["compatibility"], None)


class RemovalPlanTests(unittest.TestCase):
    def _state(self, packages: dict[str, dict]) -> dict[str, dict]:
        return packages

    def test_removing_a_package_takes_its_unwanted_dependency_with_it(self) -> None:
        harness = world()
        state = self._state({
            "test.host": {
                "id": "test.host", "version": "1.0.0", "requested": True,
                "dependencies": [API],
            },
            API: {
                "id": API, "version": "1.0.0", "requested": False, "dependencies": [],
            },
        })

        result = planner(harness, state).plan(
            ChangeRequest(("test.host",), choices={"test.host": "remove"})
        )

        self.assertEqual(result["remove"], sorted({API, "test.host"}))
        line = line_for(result, "test.host")
        self.assertEqual(line["action"], ACTION_REMOVE)
        self.assertEqual(line["remove_with"], [API])
        self.assertEqual(result["install"], {})

    def test_a_blocked_removal_swaps_the_dependent_to_a_version_that_does_not_need_it(self) -> None:
        harness = world()
        state = self._state({
            "test.host": {
                "id": "test.host", "version": "1.0.0", "requested": True,
                "dependencies": [API],
            },
            "test.loose": {
                "id": "test.loose", "version": "1.0.0", "requested": True,
                "dependencies": ["test.host"],
            },
        })

        result = planner(harness, state).plan(
            ChangeRequest(("test.host",), choices={"test.host": "remove"})
        )

        self.assertEqual(result["remove"], ["test.host"])
        self.assertEqual(result["replaced_by"], [{"id": "test.loose", "version": "0.9.0"}])
        self.assertEqual(result["install"], {"test.loose": "0.9.0"})

    def test_a_dependent_without_an_alternative_is_removed_too(self) -> None:
        harness = world()
        state = self._state({
            "test.host": {
                "id": "test.host", "version": "1.0.0", "requested": True,
                "dependencies": [API],
            },
            "test.loose": {
                "id": "test.loose", "version": "1.0.0", "requested": True,
                "dependencies": ["test.host"],
            },
        })
        harness = SolverHarness([
            candidate for candidate in harness.registry.packages if candidate.id != "test.loose"
        ] + [
            env_package(
                "test.loose",
                [env_release("1.0.0")],
                depends_on=({"id": "test.host", "version": "*", "when": "*"},),
            )
        ])

        result = planner(harness, state).plan(
            ChangeRequest(("test.host",), choices={"test.host": "remove"})
        )

        self.assertEqual(result["remove"], sorted({"test.host", "test.loose"}))


if __name__ == "__main__":
    unittest.main()
