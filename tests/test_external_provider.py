"""外部来源的行为：显式依赖选定供给者，外部主机的下载不挂到 GitHub 加速器下。"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sprocket_mod_manager.application.solver import DependencySolver
from sprocket_mod_manager.domain.errors import RegistryError
from sprocket_mod_manager.domain.models import (
    RegistryPackage,
    ReleaseAsset,
    ReleaseInfo,
)
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.domain.semver import Version
from sprocket_mod_manager.infrastructure.http_client import HttpClient

NATIVE = "test.melonloader"
BRIDGE = "test.bepinex-melonloader-bridge"
MOD = "test.mod"

NATIVE_SUPPLY = {"melonloader:mod": "{Sprocket}/Mods"}
BRIDGE_SUPPLY = {"melonloader:mod": "{Sprocket}/MLLoader/Mods"}


def package(
        package_id: str,
        *,
        supply: dict[str, str] | None = None,
        install_type: str | None = None,
        dependencies: tuple[dict[str, str], ...] = (),
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
        dependencies=tuple(dependencies),
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


class ExplicitDependencyTests(unittest.TestCase):
    def test_a_declared_dependency_picks_the_contested_provider(self) -> None:
        native = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
        bridge = package(BRIDGE, supply=BRIDGE_SUPPLY, kind="modloader")
        mod = package(
            MOD,
            install_type="melonloader:mod",
            dependencies=({"id": BRIDGE, "version": "*", "when": "*"},),
        )
        registry = Registry([native, bridge, mod])
        github = FakeGitHub({NATIVE: "1.0.0", BRIDGE: "1.0.0", MOD: "2.0.0"})

        plan = DependencySolver(registry, github).resolve(MOD)

        self.assertEqual([item.package.id for item in plan.packages], [BRIDGE, MOD])
        self.assertEqual(plan.by_id()[MOD].dependency_ids, (BRIDGE,))


class ExternalSourceRuleTests(unittest.TestCase):
    """外部下载来源只有加载器可以声明。"""

    EXTERNAL = {
        "source": {"type": "external", "hosts": ["example.org"]},
        "assets": {"include": ["*.dll"], "exclude": []},
    }

    def test_a_mod_cannot_declare_an_external_source(self) -> None:
        loader = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
        mod = package(MOD, install_type="melonloader:mod")
        mod = replace(mod, release=dict(self.EXTERNAL), releases=(release("2.0.0"),))

        with self.assertRaisesRegex(
            RegistryError, "only a modloader may declare an external release source"
        ):
            Registry([loader, mod])

    def test_a_modloader_can_declare_an_external_source(self) -> None:
        loader = package(NATIVE, supply=NATIVE_SUPPLY, kind="modloader")
        loader = replace(loader, release=dict(self.EXTERNAL), releases=(release("1.0.0"),))

        self.assertEqual(Registry([loader]).packages, (loader,))


class FakeResponse:
    def __init__(self, body: bytes, final_url: str):
        self.body = body
        self.final_url = final_url
        self.headers = {"Content-Length": str(len(body)), "ETag": "test"}
        self.read_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.final_url

    def read(self, _size):
        if self.read_count:
            return b""
        self.read_count += 1
        return self.body


class ExternalHostDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_an_external_host_is_not_rewritten_through_the_github_proxy(self) -> None:
        asset = ReleaseAsset(
            id=1,
            name="BepInEx.zip",
            size=4,
            download_url="https://builds.bepinex.dev/projects/bepinex_be/788/BepInEx.zip",
        )
        requests = []

        def respond(request, **_kwargs):
            requests.append(request)
            return FakeResponse(b"test", asset.download_url)

        with patch("sprocket_mod_manager.infrastructure.http_client.urlopen", side_effect=respond):
            HttpClient(
                self.root / "cache",
                github_proxy_url="https://mirror.example.com/",
            ).download(asset, self.root / "BepInEx.zip", hosts={"builds.bepinex.dev"})

        self.assertEqual(requests[0].full_url, asset.download_url)


if __name__ == "__main__":
    unittest.main()
