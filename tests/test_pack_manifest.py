"""整合包清单：release 里那份独立 JSON 的解析，以及"纯成员包"算不算可装。

清单是包自己的声明（成员 + 落点规则），所以解析要严：读不动就报错，不猜。纯成员包的 release 里
可以只有清单、没有载荷 —— 成员各自有自己的条目，它照样能装。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_solver import FakeGitHub, package, release

from registry_core.pack_manifest import MAX_MANIFEST_BYTES, parse_pack_manifest
from sprocket_mod_manager.application.planning import ChangePlanner, ChangeRequest
from sprocket_mod_manager.application.preparer import PlanPreparer
from sprocket_mod_manager.application.releases import installable_release, missing_manifest_data
from sprocket_mod_manager.application.solver import DependencySolver
from sprocket_mod_manager.domain.compatibility import CapabilityEnvironment
from sprocket_mod_manager.domain.errors import DownloadError, RegistryError, ScanError
from sprocket_mod_manager.domain.models import (
    PACK_KIND,
    ReleaseInfo,
    ResolutionPlan,
    ResolvedPackage,
)
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.domain.semver import Version

PACK = "test.pack"
FIRST = "test.first"


def manifest(**overrides) -> bytes:
    payload = {
        "schema_version": 1,
        "members": [{"id": FIRST, "version": "=1.0.0", "when": "*"}],
        "install": {
            "mode": "patch",
            "files": [{"match": "Parts/**", "type": "sprocket:streamingassets"}],
        },
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


class ManifestParsingTests(unittest.TestCase):
    def test_a_manifest_carries_members_and_install_rules(self):
        parsed = parse_pack_manifest(manifest())

        self.assertEqual(parsed.members, ({"id": FIRST, "version": "=1.0.0", "when": "*"},))
        self.assertEqual(parsed.install["mode"], "patch")
        self.assertFalse(parsed.is_empty)

    def test_members_and_install_are_both_optional(self):
        parsed = parse_pack_manifest(json.dumps({"schema_version": 1}).encode("utf-8"))

        self.assertEqual(parsed.members, ())
        self.assertEqual(parsed.install, {})
        self.assertTrue(parsed.is_empty)

    def test_a_missing_when_defaults_to_every_version(self):
        raw = json.dumps({
            "schema_version": 1,
            "members": [{"id": FIRST, "version": "*"}],
        }).encode("utf-8")

        self.assertEqual(parse_pack_manifest(raw).members[0]["when"], "*")

    def test_a_utf8_bom_is_tolerated(self):
        parsed = parse_pack_manifest(b"\xef\xbb\xbf" + manifest())

        self.assertEqual(parsed.members, ({"id": FIRST, "version": "=1.0.0", "when": "*"},))

    def test_a_malformed_manifest_is_an_error(self):
        cases = {
            "not JSON": b"{",
            "not an object": b"[]",
            "unknown schema": json.dumps({"schema_version": 2}).encode("utf-8"),
            "members not a list": json.dumps({"schema_version": 1, "members": {}}).encode("utf-8"),
            "member not an object": json.dumps({"schema_version": 1, "members": ["a"]}).encode("utf-8"),
            "member without a version": json.dumps(
                {"schema_version": 1, "members": [{"id": FIRST}]}
            ).encode("utf-8"),
            "member without an id": json.dumps(
                {"schema_version": 1, "members": [{"version": "*"}]}
            ).encode("utf-8"),
            "duplicate member": json.dumps({
                "schema_version": 1,
                "members": [{"id": FIRST, "version": "*"}, {"id": FIRST, "version": "*"}],
            }).encode("utf-8"),
            "install not an object": json.dumps(
                {"schema_version": 1, "install": []}
            ).encode("utf-8"),
            "unknown install mode": json.dumps(
                {"schema_version": 1, "install": {"mode": "replace"}}
            ).encode("utf-8"),
        }
        for label, raw in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ScanError):
                    parse_pack_manifest(raw)

    def test_an_oversized_manifest_is_refused_before_it_is_parsed(self):
        raw = manifest() + b" " * MAX_MANIFEST_BYTES

        with self.assertRaises(ScanError):
            parse_pack_manifest(raw)


class NoAssetsGitHub(FakeGitHub):
    """什么资产都不给（用来钉住"普通包没有可安装资产就不算可装"）。"""

    @staticmethod
    def install_assets(_package, _release):
        return ()


class PackWithoutAssetsGitHub(FakeGitHub):
    """整包那一版没有可安装资产（纯成员包的形态），别的包照常。"""

    @staticmethod
    def install_assets(package, item):
        return () if package.is_pack else item.assets


def bare_release():
    """一个没有任何资产的 release。"""
    return ReleaseInfo(1, "v1.0.0", Version.parse("1.0.0"), False, "", ())


def pack_entry(*members: dict[str, str]):
    return replace(
        package(PACK),
        kind=PACK_KIND,
        category="pack",
        members=tuple(members),
    )


class PureMemberPackTests(unittest.TestCase):
    """纯成员包：release 里只有清单、没有载荷，它照样要能进计划、能备好。"""

    def test_a_pack_release_without_assets_is_installable(self):
        entry = pack_entry({"id": FIRST, "version": "*", "when": "*"})

        self.assertTrue(installable_release(entry, bare_release(), FakeGitHub({})))
        self.assertFalse(
            installable_release(package("test.mod"), bare_release(), FakeGitHub({}))
        )

    def test_a_pure_member_pack_resolves_its_members(self):
        entry = pack_entry({"id": FIRST, "version": "*", "when": "*"})
        github = PackWithoutAssetsGitHub({
            PACK: (release("1.0.0"),),
            FIRST: (release("1.0.0"),),
        })

        plan = DependencySolver(Registry([entry, package(FIRST)]), github).resolve(PACK)

        self.assertEqual([item.package.id for item in plan.packages], [FIRST, PACK])
        self.assertEqual(plan.by_id()[PACK].dependency_ids, (FIRST,))

    def test_a_pure_member_pack_prepares_without_any_payload(self):
        entry = pack_entry({"id": FIRST, "version": "*", "when": "*"})
        plan = ResolutionPlan(
            PACK,
            (ResolvedPackage(entry, release("1.0.0"), (FIRST,)),),
            roots=(PACK,),
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preparer = PlanPreparer(root / "app", http=None, github=PackWithoutAssetsGitHub({}))
            prepared = preparer.prepare(plan, satisfied={FIRST: "1.0.0"})
            self.addCleanup(PlanPreparer.discard, prepared)

            self.assertEqual([item.resolved.package.id for item in prepared.packages], [PACK])
            self.assertEqual(prepared.packages[0].files, [])

    def test_a_normal_package_without_assets_is_still_refused(self):
        entry = package("test.mod")
        plan = ResolutionPlan(
            entry.id,
            (ResolvedPackage(entry, release("1.0.0"), ()),),
            roots=(entry.id,),
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preparer = PlanPreparer(root / "app", http=None, github=NoAssetsGitHub({}))
            with self.assertRaisesRegex(DownloadError, "no installable files"):
                preparer.prepare(plan)


class MissingManifestDataTests(unittest.TestCase):
    """索引里没有清单数据的整合包：界面上说「清单未进索引」，计划时直接报错说明原因。"""

    @staticmethod
    def pack_without_data():
        return replace(package(PACK), kind=PACK_KIND, category="pack")

    def test_a_pack_without_members_or_rules_is_flagged(self):
        github = FakeGitHub({PACK: (release("1.0.0"),)})

        self.assertTrue(missing_manifest_data(self.pack_without_data(), github))

    def test_a_pure_member_pack_is_not_flagged(self):
        entry = pack_entry({"id": FIRST, "version": "*", "when": "*"})
        github = NoAssetsGitHub({PACK: (release("1.0.0"),)})

        self.assertFalse(missing_manifest_data(entry, github))
        self.assertFalse(missing_manifest_data(package("test.mod"), github))

    def test_planning_such_a_pack_says_why(self):
        planner = ChangePlanner(
            registry=Registry([self.pack_without_data()]),
            github=FakeGitHub({PACK: (release("1.0.0"),)}),
            environment=CapabilityEnvironment(),
            state={},
            modloaders=(),
        )

        with self.assertRaisesRegex(RegistryError, "carries no manifest"):
            planner.plan(ChangeRequest(package_ids=(PACK,)))


if __name__ == "__main__":
    unittest.main()
