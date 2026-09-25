"""求解器按环境筛版本：链上一起筛、点名的版本例外、环境未知就不筛。"""

from __future__ import annotations

import unittest
from pathlib import Path

from sprocket_mod_manager.application.solver import DependencySolver
from sprocket_mod_manager.domain.compatibility import CapabilityEnvironment
from sprocket_mod_manager.domain.errors import ResolutionError
from sprocket_mod_manager.domain.models import ReleaseAsset, RegistryPackage, ReleaseInfo
from sprocket_mod_manager.domain.registry import Registry

SPROCKET_AXIS = "hamish.sprocket"


def release(version: str, *, dependencies: tuple[dict[str, str], ...] = ()) -> ReleaseInfo:
    name = f"Mod{version}.dll"
    return ReleaseInfo(
        id=int(version.replace(".", "")),
        tag=f"v{version}",
        version=_version(version),
        prerelease=False,
        published_at="",
        assets=(
            ReleaseAsset(
                id=1,
                name=name,
                size=1,
                download_url=f"https://github.com/test/repo/releases/download/v{version}/{name}",
            ),
        ),
        page_url=f"https://github.com/test/repo/releases/tag/v{version}",
        dependencies=dependencies,
    )


def _version(text: str):
    from sprocket_mod_manager.domain.semver import Version

    return Version.parse(text)


def package(
        package_id: str,
        releases: list[ReleaseInfo],
        *,
        depends_on: tuple[dict[str, str], ...] = (),
        category: str = "utility",
) -> RegistryPackage:
    return RegistryPackage(
        id=package_id,
        name=package_id,
        authors=("someone",),
        repository="test/repo",
        license="MIT",
        display_name={"en": package_id},
        description={},
        release={"version_pattern": r"^v?(\d+\.\d+\.\d+)$", "assets": {"include": ["*.dll"], "exclude": []}},
        dependencies=depends_on,
        install={},
        category=category,
        tags=(),
        releases=tuple(sorted(releases, key=lambda item: item.version, reverse=True)),
    )


class SolverHarness:
    """一套假注册表 + 假 GitHub 客户端：release 直接来自包本身，不走网络。"""

    def __init__(self, packages: list[RegistryPackage]):
        self.registry = Registry(packages)

    def github(self):
        class _GitHub:
            @staticmethod
            def releases(candidate: RegistryPackage) -> tuple[ReleaseInfo, ...]:
                return candidate.releases or ()

            @staticmethod
            def install_assets(candidate: RegistryPackage, item: ReleaseInfo) -> tuple[ReleaseAsset, ...]:
                return item.assets

        return _GitHub()


def environment(sprocket: str = "0.2.53.2", loader: str | None = "0.7.3") -> CapabilityEnvironment:
    return CapabilityEnvironment(
        sprocket=sprocket,
        sprocket_state="ok",
        capabilities={} if loader is None else {"lavagang.melonloader": loader},
        loaders={} if loader is None else {"lavagang.melonloader": loader},
        table={"entries": [{"loader": "lavagang.melonloader", "version": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}]},
    )


class EnvironmentFilterTests(unittest.TestCase):
    def test_incompatible_versions_are_not_candidates(self) -> None:
        harness = SolverHarness([
            package("test.mod", [
                release("2.0.0", dependencies=({"id": SPROCKET_AXIS, "version": ">=0.2.54.0"},)),
                release("1.0.0", dependencies=({"id": SPROCKET_AXIS, "version": "=0.2.53.2"},)),
            ]),
        ])

        plan = DependencySolver(harness.registry, harness.github(), environment=environment()).resolve("test.mod")

        self.assertEqual(str(plan.packages[0].release.version), "1.0.0")

    def test_without_an_environment_everything_is_a_candidate(self) -> None:
        harness = SolverHarness([
            package("test.mod", [
                release("2.0.0", dependencies=({"id": SPROCKET_AXIS, "version": ">=0.2.54.0"},)),
                release("1.0.0"),
            ]),
        ])

        plan = DependencySolver(harness.registry, harness.github()).resolve("test.mod")

        self.assertEqual(str(plan.packages[0].release.version), "2.0.0")

    def test_the_filter_covers_the_whole_chain(self) -> None:
        harness = SolverHarness([
            package("test.root", [release("1.0.0")], depends_on=({"id": "test.dep", "version": "*"},)),
            package("test.dep", [
                release("3.0.0", dependencies=({"id": SPROCKET_AXIS, "version": ">=0.2.54.0"},)),
                release("2.0.0", dependencies=({"id": SPROCKET_AXIS, "version": "=0.2.53.2"},)),
            ]),
        ])

        plan = DependencySolver(harness.registry, harness.github(), environment=environment()).resolve("test.root")

        versions = {item.package.id: str(item.release.version) for item in plan.packages}
        self.assertEqual(versions, {"test.root": "1.0.0", "test.dep": "2.0.0"})

    def test_a_pinned_root_keeps_its_incompatible_version(self) -> None:
        harness = SolverHarness([
            package("test.mod", [release("2.0.0", dependencies=({"id": SPROCKET_AXIS, "version": ">=0.2.54.0"},))]),
        ])

        plan = DependencySolver(
            harness.registry, harness.github(), environment=environment(), pinned=frozenset({"test.mod"})
        ).resolve("test.mod", "=2.0.0")

        self.assertEqual(str(plan.packages[0].release.version), "2.0.0", "点名要装就得能装")

    def test_an_unknown_axis_does_not_filter(self) -> None:
        harness = SolverHarness([
            package("test.mod", [
                release("2.0.0", dependencies=({"id": "lavagang.melonloader", "version": "<0.7.0"},)),
            ]),
        ])

        plan = DependencySolver(
            harness.registry, harness.github(), environment=CapabilityEnvironment(sprocket="0.2.53.2", sprocket_state="ok")
        ).resolve("test.mod")

        self.assertEqual(str(plan.packages[0].release.version), "2.0.0")

    def test_a_chain_without_any_usable_version_names_the_environment(self) -> None:
        harness = SolverHarness([
            package("test.root", [release("1.0.0")], depends_on=({"id": "test.dep", "version": "*"},)),
            package("test.dep", [release("3.0.0", dependencies=({"id": SPROCKET_AXIS, "version": ">=0.2.54.0"},))]),
        ])

        with self.assertRaises(ResolutionError) as caught:
            DependencySolver(harness.registry, harness.github(), environment=environment()).resolve("test.root")

        self.assertIn("test.dep", str(caught.exception))
        self.assertIn("3.0.0 requires another environment", str(caught.exception))
        self.assertIn("Sprocket 0.2.53.2 / lavagang.melonloader 0.7.3", str(caught.exception))

    def test_a_version_constraint_failure_does_not_blame_the_environment(self) -> None:
        harness = SolverHarness([
            package("test.root", [release("1.0.0")], depends_on=({"id": "test.dep", "version": "=9.9.9"},)),
            package("test.dep", [release("3.0.0", dependencies=({"id": SPROCKET_AXIS, "version": ">=0.2.54.0"},))]),
        ])

        with self.assertRaises(ResolutionError) as caught:
            DependencySolver(harness.registry, harness.github(), environment=environment()).resolve("test.root")

        self.assertIn("test.dep =9.9.9", str(caught.exception))
        self.assertNotIn("requires another environment", str(caught.exception))


class TranslationExemptionTests(unittest.TestCase):
    """翻译包不看自己的环境声明；它依赖的包照常判。"""

    def test_a_translation_package_is_never_filtered_by_its_own_declaration(self) -> None:
        harness = SolverHarness([
            package(
                "test.translation",
                [release("1.0.0", dependencies=({"id": SPROCKET_AXIS, "version": ">=0.9.0"},))],
                category="translation",
            ),
        ])

        plan = DependencySolver(harness.registry, harness.github(), environment=environment()).resolve(
            "test.translation"
        )

        self.assertEqual(str(plan.packages[0].release.version), "1.0.0")

    def test_its_dependency_is_still_checked(self) -> None:
        harness = SolverHarness([
            package(
                "test.translation",
                [release("1.0.0", dependencies=({"id": SPROCKET_AXIS, "version": ">=0.9.0"},))],
                depends_on=({"id": "test.dep", "version": "*"},),
                category="translation",
            ),
            package("test.dep", [release("2.0.0", dependencies=({"id": SPROCKET_AXIS, "version": ">=0.2.54.0"},))]),
        ])

        with self.assertRaises(ResolutionError) as caught:
            DependencySolver(harness.registry, harness.github(), environment=environment()).resolve(
                "test.translation"
            )

        self.assertIn("test.dep", str(caught.exception))
        self.assertIn("2.0.0 requires another environment", str(caught.exception))

    def test_the_rule_lives_in_one_place(self) -> None:
        from sprocket_mod_manager.domain.compatibility import NOT_APPLICABLE, release_verdict

        incompatible = ({"id": SPROCKET_AXIS, "version": ">=0.9.0"},)
        self.assertEqual(
            release_verdict(environment(), category="translation", dependencies=incompatible),
            NOT_APPLICABLE,
        )
        self.assertEqual(
            release_verdict(environment(), category="utility", dependencies=incompatible),
            "incompatible",
        )


class CapabilityDependencyTests(unittest.TestCase):
    """对本机能力的包级依赖不是安装边：装不了一个能力，它由能力判定负责。"""

    def test_a_local_capability_dependency_is_not_an_install_edge(self) -> None:
        harness = SolverHarness([
            package(
                "test.mod",
                [release("1.0.0")],
                depends_on=({"id": SPROCKET_AXIS, "version": ">=0.2.53.0", "when": "*"},),
            ),
        ])

        plan = DependencySolver(harness.registry, harness.github()).resolve("test.mod")

        self.assertEqual([item.package.id for item in plan.packages], ["test.mod"])
        self.assertEqual(plan.by_id()["test.mod"].dependency_ids, ())


if __name__ == "__main__":
    unittest.main()
