"""一个类型有多个供给者时的行为：装上的那个决定目录，否则显式报错，绝不猜。"""

from __future__ import annotations

import unittest

from sprocket_mod_manager.application.preparer import PlanPreparer
from sprocket_mod_manager.application.solver import DependencySolver
from sprocket_mod_manager.domain.errors import InstallError, ResolutionError, ScanError
from sprocket_mod_manager.domain.models import (
    RegistryPackage,
    ReleaseAsset,
    ReleaseInfo,
    ResolvedPackage,
    ResolutionPlan,
)
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.domain.semver import Version


def package(
        package_id: str,
        *,
        supply: dict[str, str] | None = None,
        install_type: str | None = None,
        kind: str = "modfile",
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
        dependencies=(),
        install=install,
        category="utility",
        tags=(),
        kind=kind,
        supply=dict(supply or {}),
        file_rules=file_rules,
    )


def release(version: str) -> ReleaseInfo:
    asset = ReleaseAsset(
        1, "package.dll", 1,
        "https://github.com/test/repo/releases/download/v/package.dll",
    )
    return ReleaseInfo(
        id=1,
        tag=f"v{version}",
        version=Version.parse(version),
        prerelease=False,
        published_at="",
        assets=(asset,),
    )


class FakeGitHub:
    def __init__(self, versions: dict[str, str]):
        self._versions = versions

    def releases(self, pkg: RegistryPackage) -> tuple[ReleaseInfo, ...]:
        return (release(self._versions[pkg.id]),)

    @staticmethod
    def install_assets(pkg: RegistryPackage, item: ReleaseInfo) -> tuple[ReleaseAsset, ...]:
        return item.assets


NATIVE = "test.melonloader"
BRIDGE = "test.bepinex-melonloader-bridge"
MOD = "test.mod"

NATIVE_SUPPLY = {"melonloader:mod": "{Sprocket}/Mods"}
BRIDGE_SUPPLY = {"melonloader:mod": "{Sprocket}/MLLoader/Mods"}


def contested_registry() -> tuple[Registry, list[RegistryPackage]]:
    native = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
    bridge = package(BRIDGE, supply=BRIDGE_SUPPLY, kind="modloader")
    mod = package(MOD, install_type="melonloader:mod")
    return Registry([native, bridge, mod]), [native, bridge, mod]


def github() -> FakeGitHub:
    return FakeGitHub({NATIVE: "1.0.0", BRIDGE: "1.0.0", MOD: "2.0.0"})


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
