"""「哪一版能装」只有一处判据，而且只为目录读数会列出的包算。

两条读数各算一份 latest 时口径还不一样：目录那条认只带清单的整合包，服务器那条只看可安装资产，
于是同一个包在读数里显示「无可用版本」、求解器却当它可装。这里钉住统一后的样子。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sprocket_mod_manager.application.catalog import latest_release_for, load_catalog
from sprocket_mod_manager.application.service import ModManagerService
from sprocket_mod_manager.domain.models import (
    PACK_KIND,
    ReleaseAsset,
    ReleaseInfo,
    RegistryPackage,
)
from sprocket_mod_manager.domain.semver import Version
from sprocket_mod_manager.infrastructure.config import ConfigStore

INDEX_ID = "test.index-mod"
SOURCED_ID = "test.sourced-mod"


def release(package_id: str, *, asset_name: str = "Mod.dll") -> ReleaseInfo:
    asset = ReleaseAsset(
        id=1,
        name=asset_name,
        size=4,
        download_url=(
            f"https://github.com/test/{package_id}/releases/download/v1.0.0/{asset_name}"
        ),
    )
    return ReleaseInfo(1, "v1.0.0", Version.parse("1.0.0"), False, "", (asset,))


def plain_package(package_id: str) -> RegistryPackage:
    return RegistryPackage(
        id=package_id,
        name=package_id,
        authors=("test",),
        repository=f"test/{package_id}",
        license="MIT",
        display_name={"en": package_id},
        description={"en": "test"},
        release={"assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=(),
        install={},
        category="utility",
        tags=(),
        releases=(release(package_id),),
    )


def index_entry(package_id: str) -> dict:
    """索引里的一条：发布数据内嵌，所以这一趟不联网。"""
    return {
        "schema_version": 3,
        "id": package_id,
        "name": "IndexMod",
        "repository": f"test/{package_id}",
        "install": {"files": [], "exclude": []},
        "releases": [
            {
                "id": 1,
                "version": "1.0.0",
                "assets": [
                    {
                        "id": 1,
                        "name": "Mod.dll",
                        "size": 4,
                        "download_url": (
                            f"https://github.com/test/{package_id}/releases/download/v1.0.0/Mod.dll"
                        ),
                    }
                ],
            }
        ],
    }


def members_only_pack(package_id: str = "test.members-only") -> RegistryPackage:
    """只有一个清单资产、挑不出可安装资产的整合包：成员在条目里，所以它算可装。"""
    return RegistryPackage(
        id=package_id,
        name="MembersOnly",
        authors=("test",),
        repository=f"test/{package_id}",
        license="MIT",
        display_name={"en": package_id},
        description={"en": "test"},
        release={"assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=(),
        install={},
        category="pack",
        tags=(),
        kind=PACK_KIND,
        members=({"id": "test.member", "version": "*", "when": "*"},),
        releases=(release(package_id, asset_name="pack.json"),),
    )


def index_dir(root: Path, packages: list[dict]) -> Path:
    directory = root / "registry" / "data"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "packages.json").write_text(
        json.dumps(
            {"schema_version": 1, "generated_at": "", "packages": packages},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (directory / "environment.json").write_text(
        json.dumps({"schema_version": 1, "game": {"id": "hamish.sprocket", "name": "Sprocket"}}),
        encoding="utf-8",
    )
    (directory / "diagnosis.json").write_text(
        json.dumps({"schema_version": 1, "entries": []}), encoding="utf-8"
    )
    return directory


class FakeGitHub:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def releases(self, package: RegistryPackage, refresh: bool = False) -> tuple[ReleaseInfo, ...]:
        self.asked.append(package.id)
        return package.releases or ()

    @staticmethod
    def install_assets(package: RegistryPackage, release: ReleaseInfo) -> tuple[ReleaseAsset, ...]:
        include = package.release.get("assets", {}).get("include", ["*"])
        return tuple(
            asset
            for asset in release.assets
            for pattern in include
            if Path(asset.name).match(pattern)
        )


class LatestReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.app_dir = self.root / "app"
        ConfigStore(self.app_dir).save({"language": "zh", "game_path": "", "index_url": ""})

    def test_a_members_only_pack_counts_as_installable(self) -> None:
        github = FakeGitHub()

        chosen = latest_release_for(github, members_only_pack())

        self.assertIsNotNone(chosen, "成员在条目里，这一版就能装")
        self.assertEqual(str(chosen.version), "1.0.0")

    def test_a_package_without_installable_assets_has_no_version(self) -> None:
        github = FakeGitHub()

        chosen = latest_release_for(github, plain_package("test.nothing"))

        self.assertIsNone(chosen, "挑不出可安装资产、也不是整合包：没有可装版本")

    def test_latest_is_asked_only_for_the_packages_the_catalog_lists(self) -> None:
        """索引之外来的包由它们自己那份读数去算，目录这一趟不再替它们白算一遍。"""
        service = ModManagerService(self.app_dir)
        github = FakeGitHub()
        service.github = github
        service.register_private_packages([plain_package(SOURCED_ID)])
        directory = index_dir(self.root, [index_entry(INDEX_ID)])

        service, latest = load_catalog(service, directory, refresh=False)

        self.assertIn(INDEX_ID, github.asked, "目录读数会列出的包照旧要算")
        self.assertNotIn(SOURCED_ID, github.asked, "索引之外的包不在这里算")
        self.assertIn(INDEX_ID, latest)
        self.assertTrue(service.registry.has_package(SOURCED_ID), "它照旧能按 id 找到")


if __name__ == "__main__":
    unittest.main()
