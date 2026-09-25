"""一个类型有多个供给者时的行为：唯一供给者直接用，声明了依赖用声明，否则由索引里的
「加载器包 ↔ 游戏」表按游戏版本定，表没结论才看已安装集合，都没有答案就显式报错，绝不猜。"""

from __future__ import annotations

import unittest

from sprocket_mod_manager.application.preparer import PlanPreparer
from sprocket_mod_manager.application.solver import DependencySolver
from sprocket_mod_manager.domain.compatibility import CapabilityEnvironment
from sprocket_mod_manager.domain.errors import InstallError, ResolutionError, ScanError
from sprocket_mod_manager.domain.models import (
    RegistryPackage,
    ReleaseAsset,
    ReleaseInfo,
    ResolvedPackage,
    ResolutionPlan,
)
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.domain.semver import Version, satisfies


def package(
        package_id: str,
        *,
        supply: dict[str, str] | None = None,
        install_type: str | None = None,
        kind: str = "modfile",
        depends_on: tuple[dict[str, str], ...] = (),
) -> RegistryPackage:
    install = {"scan_dlls": False, "exclude": []}
    file_rules: tuple[dict[str, str], ...] = ()
    if install_type is not None:
        install["files"] = [{"match": "**", "type": install_type, "layout": "file"}]
        file_rules = ({"match": "**", "type": install_type, "layout": "file"},)
    return RegistryPackage(
        id=package_id,
        name=package_id,
        authors=("test",),
        repository="test/repo",
        license="MIT",
        display_name={"en": package_id},
        description={"en": "test"},
        release={"assets": {"include": ["*.dll"], "exclude": []}},
        dependencies=depends_on,
        install=install,
        category="utility",
        tags=(),
        kind=kind,
        supply=dict(supply or {}),
        file_rules=file_rules,
    )


def release(version: str, *, release_id: int = 1) -> ReleaseInfo:
    asset = ReleaseAsset(
        release_id, "package.dll", 1,
        "https://github.com/test/repo/releases/download/v/package.dll",
    )
    return ReleaseInfo(
        id=release_id,
        tag=f"v{version}",
        version=Version.parse(version),
        prerelease=False,
        published_at="",
        assets=(asset,),
    )


class FakeGitHub:
    def __init__(self, versions: dict[str, str | tuple[str, ...]]):
        self._versions = versions

    def releases(self, pkg: RegistryPackage) -> tuple[ReleaseInfo, ...]:
        value = self._versions[pkg.id]
        items = (value,) if isinstance(value, str) else tuple(value)
        return tuple(release(item, release_id=index + 1) for index, item in enumerate(items))

    @staticmethod
    def install_assets(pkg: RegistryPackage, item: ReleaseInfo) -> tuple[ReleaseAsset, ...]:
        return item.assets


NATIVE = "test.melonloader"
BRIDGE = "test.bepinex-melonloader-bridge"
MOD = "test.mod"
BEPINEX = "test.bepinex"

NATIVE_SUPPLY = {"melonloader:mod": "{Sprocket}/Mods"}
BRIDGE_SUPPLY = {"melonloader:mod": "{Sprocket}/MLLoader/Mods"}

# 加载器包 ↔ 游戏：原生 MelonLoader 只到 0.2.54 之前，桥接从 0.2.54 起。
PROVIDER_TABLE = {
    "entries": [
        {"loader": NATIVE, "version": ">=1.0.0 <2.0.0", "sprocket": "<0.2.54.0"},
        {"loader": BRIDGE, "version": ">=1.0.0", "sprocket": ">=0.2.54.0"},
    ]
}


def game_environment(sprocket: str | None = "0.2.53.2", state: str = "ok") -> CapabilityEnvironment:
    return CapabilityEnvironment(sprocket=sprocket, sprocket_state=state)


def table_registry(
        table: dict | None = None,
        *,
        mod_dependencies: tuple[dict[str, str], ...] = (),
        bridge_dependencies: tuple[dict[str, str], ...] = (),
) -> Registry:
    native = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
    bridge = package(BRIDGE, supply=BRIDGE_SUPPLY, kind="modloader", depends_on=bridge_dependencies)
    bepinex = package(BEPINEX)
    mod = package(MOD, install_type="melonloader:mod", depends_on=mod_dependencies)
    return Registry(
        [native, bridge, bepinex, mod], PROVIDER_TABLE if table is None else table
    )


def contested_registry() -> tuple[Registry, list[RegistryPackage]]:
    native = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
    bridge = package(BRIDGE, supply=BRIDGE_SUPPLY, kind="modloader")
    mod = package(MOD, install_type="melonloader:mod")
    return Registry([native, bridge, mod]), [native, bridge, mod]


def github() -> FakeGitHub:
    return FakeGitHub({NATIVE: "1.0.0", BRIDGE: "1.0.0", MOD: "2.0.0", BEPINEX: "1.0.0"})


class RegistryProviderTests(unittest.TestCase):
    def test_a_type_can_have_several_providers(self) -> None:
        registry, _packages = contested_registry()

        self.assertEqual(
            [provider.id for provider in registry.providers_for_type("melonloader:mod")],
            [NATIVE, BRIDGE],
        )

    def test_the_resolver_needs_an_installed_provider_to_pick_one(self) -> None:
        registry, _packages = contested_registry()

        self.assertIsNone(registry.supplier_for_type("melonloader:mod"))
        self.assertEqual(registry.supplier_for_type("melonloader:mod", [NATIVE]).id, NATIVE)
        self.assertEqual(registry.supplier_for_type("melonloader:mod", [BRIDGE]).id, BRIDGE)

    def test_install_directories_only_covers_types_with_one_answer(self) -> None:
        registry, _packages = contested_registry()

        self.assertEqual(registry.install_directories(), {})
        resolved = registry.install_directories([BRIDGE])
        self.assertEqual(str(resolved["melonloader:mod"]), "MLLoader/Mods")

    def test_a_single_provider_needs_no_installed_set(self) -> None:
        loader = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
        mod = package(MOD, install_type="melonloader:mod")
        registry = Registry([loader, mod])

        self.assertEqual(registry.supplier_for_type("melonloader:mod").id, NATIVE)
        self.assertEqual(
            str(registry.install_directories()["melonloader:mod"]), "Mods"
        )

    def test_a_wildcard_install_type_resolves_through_the_supply_table(self) -> None:
        loader = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
        mod = package(MOD, install_type="melonloader:*")
        registry = Registry([loader, mod])

        self.assertEqual(
            [provider.id for provider in registry.providers_for_type("melonloader:*")],
            [NATIVE],
        )
        self.assertEqual(registry.supplier_for_type("melonloader:*").id, NATIVE)

    def test_a_wildcard_supply_key_is_rejected(self) -> None:
        loader = package(NATIVE, supply={"melonloader:*": "{Sprocket}/Mods"}, kind="modloader")

        with self.assertRaises(ScanError):
            Registry([loader])


class SolverProviderTests(unittest.TestCase):
    def test_a_contested_type_without_an_installed_provider_fails(self) -> None:
        registry, _packages = contested_registry()

        with self.assertRaises(ResolutionError) as raised:
            DependencySolver(registry, github()).resolve(MOD)

        message = str(raised.exception)
        self.assertIn("no modloader provider is installed for install type 'melonloader:mod'", message)
        self.assertIn(NATIVE, message)
        self.assertIn(BRIDGE, message)

    def test_an_installed_provider_is_the_only_one_added_to_the_plan(self) -> None:
        registry, _packages = contested_registry()

        plan = DependencySolver(registry, github(), installed=frozenset({BRIDGE})).resolve(MOD)

        self.assertEqual([item.package.id for item in plan.packages], [BRIDGE, MOD])
        self.assertEqual(plan.by_id()[MOD].dependency_ids, (BRIDGE,))

    def test_an_uncontested_type_still_auto_installs_its_provider(self) -> None:
        loader = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
        mod = package(MOD, install_type="melonloader:mod")

        plan = DependencySolver(Registry([loader, mod]), github()).resolve(MOD)

        self.assertEqual([item.package.id for item in plan.packages], [NATIVE, MOD])


class ProviderTableTests(unittest.TestCase):
    """多个供给者且一个都没装时，由索引里的「加载器包 ↔ 游戏」表按游戏版本定一个。"""

    def test_the_table_selects_the_provider_for_each_game_line(self) -> None:
        for sprocket, expected in (("0.2.53.2", NATIVE), ("0.2.54.0", BRIDGE)):
            with self.subTest(sprocket=sprocket):
                plan = DependencySolver(
                    table_registry(), github(), environment=game_environment(sprocket)
                ).resolve(MOD)

                ids = [item.package.id for item in plan.packages]
                self.assertIn(expected, ids)
                self.assertEqual(
                    [item for item in ids if item in {NATIVE, BRIDGE}], [expected]
                )
                self.assertEqual(plan.by_id()[MOD].dependency_ids, (expected,))

    def test_the_selected_bridge_pulls_in_its_own_dependency(self) -> None:
        registry = table_registry(
            bridge_dependencies=({"id": BEPINEX, "version": "*", "when": "*"},)
        )

        plan = DependencySolver(
            registry, github(), environment=game_environment("0.2.54.0")
        ).resolve(MOD)

        self.assertEqual(
            [item.package.id for item in plan.packages], [BEPINEX, BRIDGE, MOD]
        )

    def test_a_declared_dependency_beats_the_table(self) -> None:
        registry = table_registry(
            mod_dependencies=({"id": NATIVE, "version": "*", "when": "*"},)
        )

        plan = DependencySolver(
            registry, github(), environment=game_environment("0.2.54.0")
        ).resolve(MOD)

        self.assertEqual(plan.by_id()[MOD].dependency_ids, (NATIVE,))
        self.assertIn(NATIVE, [item.package.id for item in plan.packages])

    def test_an_unknown_game_version_keeps_the_current_error(self) -> None:
        for environment in (None, game_environment(sprocket=None, state="unconfigured")):
            with self.subTest(environment=environment):
                with self.assertRaises(ResolutionError) as raised:
                    DependencySolver(table_registry(), github(), environment=environment).resolve(MOD)

                message = str(raised.exception)
                self.assertIn(
                    "no modloader provider is installed for install type 'melonloader:mod'",
                    message,
                )
                self.assertIn(NATIVE, message)
                self.assertIn(BRIDGE, message)

    def test_the_row_version_range_constrains_the_loader_release(self) -> None:
        versions = FakeGitHub({NATIVE: ("2.0.0", "1.5.0"), BRIDGE: "1.0.0", MOD: "2.0.0"})

        plan = DependencySolver(
            table_registry(), versions, environment=game_environment("0.2.53.2")
        ).resolve(MOD)

        self.assertEqual(str(plan.by_id()[NATIVE].release.version), "1.5.0")

    def test_a_row_no_release_satisfies_is_a_resolution_error(self) -> None:
        table = {
            "entries": [
                {"loader": NATIVE, "version": ">=3.0.0", "sprocket": "<0.2.54.0"},
                {"loader": BRIDGE, "version": ">=1.0.0", "sprocket": ">=0.2.54.0"},
            ]
        }

        with self.assertRaises(ResolutionError) as raised:
            DependencySolver(
                table_registry(table), github(), environment=game_environment("0.2.53.2")
            ).resolve(MOD)

        message = str(raised.exception)
        self.assertIn("no compatible release set found", message)
        self.assertIn(NATIVE, message)

    def test_no_provider_for_the_game_version_names_the_type_and_the_reason(self) -> None:
        table = {
            "entries": [
                {"loader": NATIVE, "version": ">=1.0.0", "sprocket": "<0.2.50.0"},
                {"loader": BRIDGE, "version": ">=1.0.0", "sprocket": ">=0.3.0"},
            ]
        }

        with self.assertRaises(ResolutionError) as raised:
            DependencySolver(
                table_registry(table), github(), environment=game_environment("0.2.53.2")
            ).resolve(MOD)

        message = str(raised.exception)
        self.assertIn("melonloader:mod", message)
        self.assertIn(NATIVE, message)
        self.assertIn(BRIDGE, message)
        self.assertIn("targets this game version", message)

    def test_a_table_that_leaves_several_falls_back_to_the_installed_provider(self) -> None:
        table = {
            "entries": [
                {"loader": NATIVE, "version": ">=1.0.0", "sprocket": "<0.2.54.0"},
                {"loader": BRIDGE, "version": ">=1.0.0", "sprocket": "<0.2.54.0"},
            ]
        }
        registry = table_registry(table)

        plan = DependencySolver(
            registry,
            github(),
            environment=game_environment("0.2.53.2"),
            installed=frozenset({BRIDGE}),
        ).resolve(MOD)
        self.assertEqual(plan.by_id()[MOD].dependency_ids, (BRIDGE,))

        with self.assertRaises(ResolutionError) as raised:
            DependencySolver(
                registry,
                github(),
                environment=game_environment("0.2.53.2"),
                installed=frozenset({NATIVE, BRIDGE}),
            ).resolve(MOD)

        message = str(raised.exception)
        self.assertIn("several modloader providers are installed", message)
        self.assertIn(NATIVE, message)
        self.assertIn(BRIDGE, message)

    def test_several_rows_for_one_loader_union_their_version_ranges(self) -> None:
        single = [{"loader": NATIVE, "version": ">=1.0.0 <2.0.0", "sprocket": "<0.2.54.0"}]
        self.assertEqual(DependencySolver._rows_version_range(single), ">=1.0.0 <2.0.0")
        self.assertEqual(
            DependencySolver._rows_version_range([single[0], single[0]]), ">=1.0.0 <2.0.0"
        )

        both = [
            {"loader": NATIVE, "version": ">=6.0.0-be.785", "sprocket": "<0.2.54.0"},
            {"loader": NATIVE, "version": ">=1.0.0 <2.0.0", "sprocket": "<0.2.54.0"},
        ]
        union = DependencySolver._rows_version_range(both)
        self.assertTrue(satisfies("1.5.0", union))
        self.assertTrue(satisfies("6.0.0-be.800", union))

        for unconstrained in (
            [{"loader": NATIVE, "version": "*", "sprocket": "<0.2.54.0"}],
            [{"loader": NATIVE, "sprocket": "<0.2.54.0"}],
        ):
            self.assertEqual(DependencySolver._rows_version_range(unconstrained), "*")

    def test_a_loader_release_in_a_covered_window_is_selectable(self) -> None:
        table = {
            "entries": [
                {"loader": NATIVE, "version": ">=6.0.0-be.785", "sprocket": "<0.2.54.0"},
                {"loader": NATIVE, "version": ">=1.0.0 <2.0.0", "sprocket": "<0.2.54.0"},
                {"loader": BRIDGE, "version": ">=1.0.0", "sprocket": ">=0.2.54.0"},
            ]
        }
        versions = FakeGitHub({NATIVE: ("1.5.0",), BRIDGE: "1.0.0", MOD: "2.0.0"})

        plan = DependencySolver(
            table_registry(table), versions, environment=game_environment("0.2.53.2")
        ).resolve(MOD)

        self.assertEqual(str(plan.by_id()[NATIVE].release.version), "1.5.0")


class PlanDirectoryTests(unittest.TestCase):
    def test_a_plan_carrying_two_providers_of_one_type_is_rejected(self) -> None:
        native = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
        bridge = package(BRIDGE, supply=BRIDGE_SUPPLY, kind="modloader")
        plan = ResolutionPlan(
            root_id=MOD,
            packages=(
                ResolvedPackage(native, release("1.0.0"), ()),
                ResolvedPackage(bridge, release("1.0.0"), ()),
            ),
        )

        with self.assertRaises(InstallError) as raised:
            PlanPreparer.install_directories(plan)

        message = str(raised.exception)
        self.assertIn("melonloader:mod", message)
        self.assertIn(NATIVE, message)
        self.assertIn(BRIDGE, message)

    def test_a_plan_with_one_provider_per_type_resolves_directories(self) -> None:
        bridge = package(BRIDGE, supply=BRIDGE_SUPPLY, kind="modloader")
        plan = ResolutionPlan(
            root_id=MOD,
            packages=(ResolvedPackage(bridge, release("1.0.0"), ()),),
        )

        directories = PlanPreparer.install_directories(plan)

        self.assertEqual(str(directories["melonloader:mod"]), "MLLoader/Mods")


if __name__ == "__main__":
    unittest.main()
