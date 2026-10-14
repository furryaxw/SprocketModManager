"""整合包：成员在盘上的要求与依赖同一套口径。

包是 `kind: "pack"` 的条目，`members` 与 `dependencies` 同形状、同 `when` 语义。求解、落盘、
卸载级联都按依赖走，所以这里固定三件事：解析进模型、按包自己这一版过 `when`、计划页把成员
归到包名下。
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_solver import FakeGitHub, package, release

from sprocket_mod_manager.application.planning import ChangePlanner, ChangeRequest
from sprocket_mod_manager.application.solver import DependencySolver
from sprocket_mod_manager.domain.compatibility import CapabilityEnvironment
from sprocket_mod_manager.domain.errors import ResolutionError
from sprocket_mod_manager.domain.models import PACK_KIND, RegistryPackage
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.utilities.dependencies import dependencies_for_release

PACK = "test.pack"
FIRST = "test.first"
SECOND = "test.second"


def member(package_id: str, version: str = "*", when: str = "*") -> dict[str, str]:
    return {"id": package_id, "version": version, "when": when}


def pack_entry(*members: dict[str, str], **overrides) -> RegistryPackage:
    return replace(
        package(PACK),
        kind=PACK_KIND,
        category="pack",
        members=tuple(members),
        **overrides,
    )


class PackModelTests(unittest.TestCase):
    @staticmethod
    def entry(**overrides) -> dict:
        return {
            "schema_version": 3,
            "id": "example.pack",
            "name": "ExamplePack",
            "authors": ["ExampleAuthor"],
            "repository": "ExampleAuthor/ExamplePack",
            "license": "MIT",
            "dependencies": [],
            "install": {},
            "kind": "pack",
            "members": [member("example.first", "=1.2.0")],
            **overrides,
        }

    def test_members_parse_into_the_model(self):
        parsed = RegistryPackage.from_dict(self.entry())

        self.assertTrue(parsed.is_pack)
        self.assertEqual(parsed.members, (member("example.first", "=1.2.0"),))
        self.assertEqual(parsed.requirements(), (member("example.first", "=1.2.0"),))

    def test_members_only_count_for_a_pack(self):
        """别的种类写了 `members` 也不当数：注册表 CI 会拦下这种条目，客户端不另造语义。"""
        parsed = RegistryPackage.from_dict(self.entry(kind="modfile"))

        self.assertFalse(parsed.is_pack)
        self.assertEqual(parsed.members, (member("example.first", "=1.2.0"),))
        self.assertEqual(parsed.requirements(), ())

    def test_a_member_only_applies_to_the_releases_its_when_covers(self):
        entry = pack_entry(member(FIRST, "*", ">=2.0.0"))
        first = package(FIRST)

        self.assertEqual(
            dependencies_for_release(entry, release("2.0.0")),
            (member(FIRST, "*", ">=2.0.0"),),
        )
        self.assertEqual(dependencies_for_release(entry, release("1.0.0")), ())


class PackResolutionTests(unittest.TestCase):
    def test_members_resolve_as_dependencies_of_the_pack(self):
        entry = pack_entry(member(FIRST), member(SECOND, ">=1.0.0"))
        github = FakeGitHub({
            PACK: (release("1.0.0"),),
            FIRST: (release("1.0.0"),),
            SECOND: (release("1.2.0"), release("1.0.0")),
        })

        plan = DependencySolver(
            Registry([entry, package(FIRST), package(SECOND)]), github
        ).resolve(PACK)

        self.assertEqual([item.package.id for item in plan.packages], [FIRST, SECOND, PACK])
        self.assertEqual(plan.by_id()[PACK].dependency_ids, (FIRST, SECOND))
        self.assertEqual(str(plan.by_id()[SECOND].release.version), "1.2.0")

    def test_a_member_version_range_is_enforced_and_named(self):
        entry = pack_entry(member(FIRST, ">=2.0.0"))
        github = FakeGitHub({PACK: (release("1.0.0"),), FIRST: (release("1.0.0"),)})

        with self.assertRaisesRegex(ResolutionError, ">=2.0.0"):
            DependencySolver(Registry([entry, package(FIRST)]), github).resolve(PACK)

    def test_a_member_gated_to_a_newer_pack_release_lets_the_older_release_win(self):
        entry = pack_entry(member(SECOND, ">=2.0.0", ">=2.0.0"))
        github = FakeGitHub({
            PACK: (release("2.0.0"), release("1.0.0")),
            SECOND: (release("1.0.0"),),
        })

        plan = DependencySolver(
            Registry([entry, package(SECOND)]), github
        ).resolve(PACK)

        self.assertEqual(str(plan.by_id()[PACK].release.version), "1.0.0")
        self.assertNotIn(SECOND, plan.by_id(), "这一版不要求它，不该被拉进来")

    def test_a_pack_is_planned_as_a_pack_not_a_dependency_of_its_members(self):
        entry = pack_entry(member(FIRST))
        github = FakeGitHub({PACK: (release("1.0.0"),), FIRST: (release("1.0.0"),)})

        plan = DependencySolver(Registry([entry, package(FIRST)]), github).resolve(FIRST)

        self.assertEqual([item.package.id for item in plan.packages], [FIRST])
        self.assertEqual(plan.by_id()[FIRST].dependency_ids, ())


class PackPlanPayloadTests(unittest.TestCase):
    def test_the_plan_groups_members_under_their_pack(self):
        entry = pack_entry(member(FIRST), member(SECOND, ">=1.0.0"))
        registry = Registry([entry, package(FIRST), package(SECOND)])
        github = FakeGitHub({
            PACK: (release("1.0.0"),),
            FIRST: (release("1.0.0"),),
            SECOND: (release("1.0.0"),),
        })
        planner = ChangePlanner(
            registry=registry,
            github=github,
            environment=CapabilityEnvironment(),
            state={},
        )

        payload = planner.plan(ChangeRequest(package_ids=(PACK,)))

        self.assertEqual(
            payload["pack_members"], {PACK: [FIRST, SECOND]}
        )
        lines = {line["id"]: line for line in payload["lines"]}
        self.assertEqual(lines[PACK]["member_of"], [])
        self.assertEqual(lines[FIRST]["member_of"], [PACK])
        self.assertEqual(lines[SECOND]["member_of"], [PACK])
        self.assertEqual(
            sorted(payload["install"]), [FIRST, PACK, SECOND],
            "成员跟着包一起进这一批要落盘的清单",
        )

    def test_a_pack_with_an_uninstallable_member_is_dropped_with_a_reason(self):
        """包是一组：成员装不上时整包不装，红行指名是哪个成员。"""
        entry = pack_entry(member(FIRST, ">=2.0.0"))
        registry = Registry([entry, package(FIRST)])
        github = FakeGitHub({PACK: (release("1.0.0"),), FIRST: (release("1.0.0"),)})
        planner = ChangePlanner(
            registry=registry,
            github=github,
            environment=CapabilityEnvironment(),
            state={},
        )

        payload = planner.plan(ChangeRequest(package_ids=(PACK,)))

        lines = {line["id"]: line for line in payload["lines"]}
        self.assertEqual(lines[PACK]["action"], "skip")
        self.assertEqual(payload["install"], {})
        self.assertEqual([item["kind"] for item in lines[PACK]["problems"]], ["missing-dependency"])
        self.assertEqual(lines[PACK]["problems"][0]["others"], [FIRST])

    def test_a_pack_line_reports_the_version_range_of_each_member(self):
        entry = pack_entry(member(FIRST, "=1.0.0"))
        registry = Registry([entry, package(FIRST)])
        github = FakeGitHub({
            PACK: (release("1.0.0"),),
            FIRST: (release("1.0.0"), release("1.1.0")),
        })
        planner = ChangePlanner(
            registry=registry,
            github=github,
            environment=CapabilityEnvironment(),
            state={},
        )

        payload = planner.plan(ChangeRequest(package_ids=(PACK,)))

        lines = {line["id"]: line for line in payload["lines"]}
        self.assertEqual(lines[FIRST]["version"], "1.0.0", "成员按包给的区间钉住")
        self.assertEqual(lines[PACK]["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
