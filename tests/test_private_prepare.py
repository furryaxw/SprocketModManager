"""私有包走它自己的下载口：公开的 HTTP 客户端不该被私有地址碰到。

私有条目的下载地址指向某台开发者服务器，取回要带会话、还要过 `download_origins`；
`HttpClient` 的主机白名单里没有那台服务器，所以接错管道会在下载那一刻才炸。
"""

from __future__ import annotations

import io
import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.application.preparer import PlanPreparer  # noqa: E402
from sprocket_mod_manager.domain.models import (  # noqa: E402
    MODFILE_KIND,
    MODLOADER_KIND,
    RegistryPackage,
    ReleaseAsset,
    ReleaseInfo,
    ResolvedPackage,
    ResolutionPlan,
)
from sprocket_mod_manager.domain.semver import Version  # noqa: E402

LOADER = "test.loader"
MOD = "team1.private-mod"
MOD_TYPE = "melonloader:mod"
SERVER_URL = "http://127.0.0.1:8787"
GITHUB_ASSET = "https://github.com/test/repo/releases/download/v1.0.0/loader.zip"


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        for name, content in entries.items():
            output.writestr(name, content)
    return buffer.getvalue()


ARCHIVE = zip_bytes({"Mod.dll": b"mod"})
LOADER_ARCHIVE = zip_bytes({"MelonLoader/net6/MelonLoader.dll": b"loader"})
# 服务端下发的 digest 就是正文的 SHA-256；摘要不符必须拦住，所以这里用真值。
ARCHIVE_SHA256 = hashlib.sha256(ARCHIVE).hexdigest()


def loader_package() -> RegistryPackage:
    """公开加载器：它供给私有模组要用的安装类型，载荷走安装计划那条树。"""
    return RegistryPackage(
        id=LOADER,
        name="Loader",
        authors=("test",),
        repository="test/loader",
        license="MIT",
        display_name={"en": "Loader"},
        description={"en": "test loader"},
        release={"assets": {"include": ["*.zip"], "exclude": []}},
        dependencies=(),
        install={"payload": [{"match": "**", "target": "{Sprocket}", "layout": "tree"}]},
        category="utility",
        tags=(),
        kind=MODLOADER_KIND,
        supply={MOD_TYPE: "{Sprocket}/Mods"},
        payload_rules=({"match": "**", "target": "{Sprocket}", "layout": "tree"},),
    )


def loader_release() -> ReleaseInfo:
    asset = ReleaseAsset(2, "loader.zip", len(LOADER_ARCHIVE), GITHUB_ASSET)
    return ReleaseInfo(2, "v1.0.0", Version.parse("1.0.0"), False, "", (asset,))


def private_package() -> RegistryPackage:
    """私有条目：`repository` 未知时服务端会省略它，`release` 也被剪掉 —— 只有 `install` 与 releases。"""
    rule = {"match": "**", "type": MOD_TYPE, "layout": "tree"}
    return RegistryPackage(
        id=MOD,
        name="PrivateMod",
        authors=("team1",),
        repository="",
        license="Private distribution",
        display_name={"en": "Private Mod"},
        description={"en": "private test mod"},
        release={},
        dependencies=(),
        install={"files": [rule], "scan_dlls": False, "exclude": []},
        category="other",
        tags=(),
        kind=MODFILE_KIND,
        schema_version=3,
        file_rules=(rule,),
    )


def private_release() -> ReleaseInfo:
    asset = ReleaseAsset(
        1,
        "mod.zip",
        len(ARCHIVE),
        f"{SERVER_URL}/v1/packages/{MOD}/download",
        digest=f"sha256:{ARCHIVE_SHA256}",
    )
    return ReleaseInfo(1, "v1.0.0", Version.parse("1.0.0"), False, "", (asset,))


class FakeGitHub:
    @staticmethod
    def install_assets(_package: RegistryPackage, release: ReleaseInfo) -> tuple[ReleaseAsset, ...]:
        return release.assets


class FakeHttp:
    """公开下载口：真的被私有地址碰到就该记下来。"""

    def __init__(self) -> None:
        self.downloaded: list[str] = []

    def download(self, asset: ReleaseAsset, destination: Path, *, progress=None, hosts=None):
        self.downloaded.append(asset.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(LOADER_ARCHIVE if asset.name == "loader.zip" else ARCHIVE)
        return destination


class FakePrivateAssets:
    def __init__(self, *, owns: bool = True) -> None:
        self.owns = owns
        self.downloaded: list[str] = []

    def handles(self, package: RegistryPackage) -> bool:
        return self.owns and package.id == MOD

    def download(self, package, release, asset, destination, progress=None) -> None:
        self.downloaded.append(asset.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(ARCHIVE)


class PrivatePrepareTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.plan = ResolutionPlan(
            MOD,
            (
                ResolvedPackage(loader_package(), loader_release(), ()),
                ResolvedPackage(private_package(), private_release(), (LOADER,)),
            ),
        )
        self.http = FakeHttp()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def prepare(self, private: FakePrivateAssets | None):
        preparer = PlanPreparer(self.root / "app", self.http, FakeGitHub(), private)
        prepared = preparer.prepare(self.plan)
        self.addCleanup(PlanPreparer.discard, prepared)
        return prepared

    def test_a_private_package_is_fetched_through_its_own_source(self) -> None:
        private = FakePrivateAssets()

        prepared = self.prepare(private)

        self.assertEqual(private.downloaded, ["mod.zip"])
        self.assertEqual(
            self.http.downloaded, ["loader.zip"], "同一个计划里的公开加载器仍走公开口"
        )
        self.assertEqual(
            [item.resolved.package.id for item in prepared.packages], [LOADER, MOD]
        )
        self.assertEqual(
            [item.target for item in prepared.packages[1].files], ["Mods/Mod.dll"]
        )

    def test_the_manifest_digest_is_the_expected_checksum(self) -> None:
        prepared = self.prepare(FakePrivateAssets())

        asset = next(
            item for item in prepared.packages if item.resolved.package.id == MOD
        ).assets[0]

        self.assertTrue(asset.publisher_verified, "服务端下发的 digest 就是期望的摘要")
        self.assertEqual(asset.publisher_digest, ARCHIVE_SHA256)

    def test_a_source_that_does_not_own_the_package_is_skipped(self) -> None:
        """下载口在场但不管这个包时，必须退回公开路径，不能悄悄什么都不下。"""
        private = FakePrivateAssets(owns=False)

        self.prepare(private)

        self.assertEqual(private.downloaded, [])
        self.assertEqual(self.http.downloaded, ["loader.zip", "mod.zip"])

    def test_without_a_private_source_everything_goes_public(self) -> None:
        self.prepare(None)

        self.assertEqual(self.http.downloaded, ["loader.zip", "mod.zip"])


if __name__ == "__main__":
    unittest.main()
