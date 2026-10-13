"""安装时按世界求解：和环境、已装模组、同批待安装模组一起解出一组互相兼容的版本。

只看一个根时，已装包的需求根本不进约束图 —— 装一个新包会把已装包的依赖降级掉，而且
没有任何报错。这里固化世界口径：已装包参与求解、优先保留它们当前的版本；本次点名的包
优先让路（先动它自己的版本，再考虑动已装的）；同批点名的根互相约束；真的无解才报错，
并指名冲突双方。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_solver import FakeGitHub, package, release  # noqa: E402
from test_solver_environment import (  # noqa: E402
    SolverHarness,
    environment,
    package as env_package,
    release as env_release,
)

from sprocket_mod_manager.application.solver import DependencySolver  # noqa: E402
from sprocket_mod_manager.domain.errors import ResolutionError  # noqa: E402
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402

API = "test.api"
HOST = "test.host"          # 已在场：只要 api 1.x
NEWMOD = "test.newmod"      # 已在场：不碰 api
OLD = "test.old"            # 本次要装：2.0.0 要 api 0.3.x，1.0.0 不要 api
OTHER = "test.other"        # 同批的另一个根：要 api 1.x
SPROCKET_AXIS = "hamish.sprocket"


def api_dependency(version_range: str, when: str = "*") -> dict[str, str]:
    return {"id": API, "version": version_range, "when": when}


def world() -> tuple[Registry, FakeGitHub]:
    registry = Registry([
        package(API),
        package(HOST, (api_dependency(">=1.0.0 <2.0.0"),)),
        package(NEWMOD),
        package(OLD, (api_dependency(">=0.3.0 <1.0.0", ">=2.0.0"),)),
        package(OTHER, (api_dependency(">=1.0.0 <2.0.0"),)),
    ])
    github = FakeGitHub({
        API: (release("1.0.0"), release("0.3.0")),
        HOST: (release("2.0.0"), release("1.0.0")),
        NEWMOD: (release("1.0.0"),),
        OLD: (release("2.0.0"), release("1.0.0")),
        OTHER: (release("1.0.0"),),
    })
    return registry, github


class WorldConstraintTests(unittest.TestCase):
    def solver(self, installed):
        registry, github = world()
        return DependencySolver(registry, github, installed=installed)

    def test_the_named_root_gives_way_to_keep_the_installed_world_working(self) -> None:
        plan = self.solver({HOST: "1.0.0", API: "1.0.0"}).resolve(OLD)
        by_id = plan.by_id()

        self.assertEqual(str(by_id[OLD].release.version), "1.0.0")
        self.assertNotIn(API, by_id, "api 没有变化，就不该进这次要落的盘")

    def test_the_installed_world_keeps_the_version_it_already_has(self) -> None:
        plan = self.solver({NEWMOD: "1.0.0", API: "1.0.0"}).resolve(OTHER)
        by_id = plan.by_id()

        self.assertEqual(str(by_id[API].release.version), "1.0.0", "已装的那版优先，不跟着去动它")
        self.assertNotIn(NEWMOD, by_id)

    def test_a_batch_is_solved_against_itself(self) -> None:
        plan = self.solver({}).resolve_all({OTHER: "*", OLD: "*"})
        by_id = plan.by_id()

        self.assertEqual(str(by_id[OTHER].release.version), "1.0.0")
        self.assertEqual(str(by_id[API].release.version), "1.0.0")
        self.assertEqual(str(by_id[OLD].release.version), "1.0.0", "同一批里也要挑出能共存的版本")
        self.assertEqual(set(plan.roots), {OTHER, OLD})

    def test_an_installed_package_moves_when_that_is_the_only_way(self) -> None:
        plan = self.solver({API: "0.3.0"}).resolve(OTHER)
        by_id = plan.by_id()

        self.assertEqual(str(by_id[OTHER].release.version), "1.0.0")
        self.assertEqual(str(by_id[API].release.version), "1.0.0", "只有移动已装包才装得上时，允许移动")

    def test_an_impossible_world_names_both_sides(self) -> None:
        with self.assertRaises(ResolutionError) as caught:
            self.solver({}).resolve_all({OTHER: "*", OLD: "=2.0.0"})

        message = str(caught.exception)
        self.assertIn(OTHER, message)
        self.assertIn(OLD, message)
        self.assertIn(">=1.0.0 <2.0.0", message)
        self.assertIn(">=0.3.0 <1.0.0", message)

    def test_an_installed_package_the_registry_does_not_know_is_left_alone(self) -> None:
        plan = self.solver({"test.ghost": "1.0.0"}).resolve(OLD)
        by_id = plan.by_id()

        self.assertNotIn("test.ghost", by_id)
        self.assertEqual(str(by_id[OLD].release.version), "2.0.0")


class InstalledEnvironmentTests(unittest.TestCase):
    """已装的那一版不因为环境判定被淘汰：否则机器上留着一个跑不了的老包，就再也装不进新东西。"""

    def test_an_installed_version_the_environment_rejects_does_not_block_a_new_mod(self) -> None:
        harness = SolverHarness([
            env_package("test.stale", [
                env_release("1.0.0", dependencies=({"id": SPROCKET_AXIS, "version": "=0.2.52.0"},)),
            ]),
            env_package("test.fresh", [env_release("1.0.0")]),
        ])

        plan = DependencySolver(
            harness.registry,
            harness.github(),
            environment=environment(),
            installed={"test.stale": "1.0.0"},
        ).resolve("test.fresh")

        self.assertEqual(str(plan.by_id()["test.fresh"].release.version), "1.0.0")


CONFLICT = "test.conflict"      # 只有一个版本，无条件要 api 0.3.x
EXTRA = "test.extra"            # 要 api 1.x，与冲突无关


def conflict_world() -> tuple[Registry, FakeGitHub]:
    registry = Registry([
        package(API),
        package(OTHER, (api_dependency(">=1.0.0 <2.0.0"),)),
        package(CONFLICT, (api_dependency(">=0.3.0 <1.0.0"),)),
        package(EXTRA, (api_dependency(">=1.0.0 <2.0.0"),)),
    ])
    github = FakeGitHub({
        API: (release("1.0.0"), release("0.3.0")),
        OTHER: (release("1.0.0"),),
        CONFLICT: (release("1.0.0"),),
        EXTRA: (release("1.0.0"),),
    })
    return registry, github


class TolerantResolutionTests(unittest.TestCase):
    """冲突时的让路规则：同色全出局 / 一兼容一不兼容则兼容方赢，且永不报错。"""

    def solver(self):
        registry, github = conflict_world()
        return DependencySolver(registry, github)

    def test_a_mutual_conflict_takes_both_sides_out(self) -> None:
        result = self.solver().resolve_tolerant({OTHER: "*", CONFLICT: "*"})

        self.assertIsNotNone(result.plan)
        self.assertEqual(result.plan.packages, (), "互相不能共存时谁都不装")
        self.assertEqual({item.package_id for item in result.dropped}, {OTHER, CONFLICT})

    def test_a_root_uninvolved_in_the_conflict_is_still_installed(self) -> None:
        result = self.solver().resolve_tolerant({OTHER: "*", CONFLICT: "*", EXTRA: "*"})

        self.assertIsNotNone(result.plan)
        self.assertEqual(
            sorted(item.package.id for item in result.plan.packages),
            [API, EXTRA],
            "冲突的两边都不装，与冲突无关的根和它的依赖照装",
        )
        self.assertEqual({item.package_id for item in result.dropped}, {OTHER, CONFLICT})

    def test_a_compatible_root_wins_and_the_incompatible_one_is_installed_anyway(self) -> None:
        harness = SolverHarness([
            env_package("test.api", [env_release("1.0.0"), env_release("0.3.0")]),
            # 兼容的那个要 1.x；不兼容的那个（自己跑不起来）要 0.3.x，于是两者冲突。
            env_package(
                "test.compatible",
                [
                    env_release(
                        "1.0.0", dependencies=({"id": SPROCKET_AXIS, "version": "=0.2.53.2"},)
                    )
                ],
                depends_on=({"id": "test.api", "version": ">=1.0.0", "when": "*"},),
            ),
            env_package(
                "test.broken",
                [
                    env_release(
                        "1.0.0",
                        dependencies=(({"id": SPROCKET_AXIS, "version": "=0.2.52.0"}),),
                    )
                ],
                depends_on=({"id": "test.api", "version": "<1.0.0", "when": "*"},),
            ),
        ])

        result = DependencySolver(
            harness.registry, harness.github(), environment=environment()
        ).resolve_tolerant({"test.compatible": "*", "test.broken": "*"})

        self.assertIsNotNone(result.plan)
        by_id = result.plan.by_id()
        self.assertEqual(str(by_id["test.api"].release.version), "1.0.0", "兼容那方的依赖赢")
        self.assertIn("test.broken", by_id, "不兼容那方照装")
        self.assertEqual(result.unmet, (("test.broken", "test.api"),))
        self.assertEqual(result.dropped, ())

    def test_a_named_version_inside_a_conflict_has_no_solution(self) -> None:
        result = self.solver().resolve_tolerant({OTHER: "*", CONFLICT: "=1.0.0"})

        self.assertIsNone(result.plan, "点名的那一版掺在冲突里：交给调用方保留上一个有效解")

    def test_the_conflict_comes_with_both_sides(self) -> None:
        registry, github = conflict_world()
        with self.assertRaises(ResolutionError) as caught:
            DependencySolver(registry, github).resolve_all({OTHER: "*", CONFLICT: "*"})

        conflicts = caught.exception.conflicts
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["package_id"], API)
        self.assertEqual(
            sorted(item["source"] for item in conflicts[0]["requirements"]), [CONFLICT, OTHER]
        )


if __name__ == "__main__":
    unittest.main()
