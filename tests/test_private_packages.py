"""私有包的归属与下载路由：谁下发的索引，谁负责取回。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.application.private_packages import PrivatePackageSource
from sprocket_mod_manager.domain.errors import DownloadError
from sprocket_mod_manager.domain.models import (
    MODFILE_KIND,
    RegistryPackage,
    ReleaseAsset,
    ReleaseInfo,
)
from sprocket_mod_manager.domain.semver import Version
from sprocket_mod_manager.infrastructure.private_servers import DeveloperServerError


def package(package_id: str) -> RegistryPackage:
    rule = {"match": "**", "type": "melonloader:mod", "layout": "tree"}
    return RegistryPackage(
        id=package_id,
        name="Mod",
        authors=("team1",),
        repository="",
        license="Private distribution",
        display_name={"en": "Mod"},
        description={"en": "private"},
        release={},
        dependencies=(),
        install={"files": [rule], "scan_dlls": False, "exclude": []},
        category="other",
        tags=(),
        kind=MODFILE_KIND,
        schema_version=3,
        file_rules=(rule,),
    )


def release() -> ReleaseInfo:
    asset = ReleaseAsset(1, "mod.zip", 3, "http://127.0.0.1:8787/v1/packages/a.b/download")
    return ReleaseInfo(1, "v1.0.0", Version.parse("1.0.0"), False, "", (asset,))


class FakeClient:
    def __init__(self, *, fail: str = "", packages: list[dict] | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail = fail
        self.packages_result = packages if packages is not None else []
        self.package_calls = 0

    def packages(self) -> list[dict]:
        self.package_calls += 1
        return list(self.packages_result)

    def download(self, package_id, version, destination, *, url="", progress=None) -> int:
        self.calls.append((package_id, version, str(destination), url))
        if self.fail:
            raise DeveloperServerError(self.fail, status=403, code="download_denied")
        return 3


class PrivatePackageSourceTests(unittest.TestCase):
    def source(self) -> tuple[PrivatePackageSource, FakeClient, FakeClient]:
        first, second = FakeClient(), FakeClient()
        return PrivatePackageSource({"server-a": first, "server-b": second}), first, second

    def test_a_learned_package_is_owned(self) -> None:
        source, _first, _second = self.source()

        source.learn("server-a", [{"id": "team1.one"}])

        self.assertTrue(source.handles(package("team1.one")))
        self.assertEqual(source.owner_of("team1.one"), "server-a")

    def test_an_unknown_package_is_not_owned(self) -> None:
        source, _first, _second = self.source()

        self.assertFalse(source.handles(package("team1.nobody")))

    def test_download_routes_to_the_owning_server(self) -> None:
        source, first, second = self.source()
        source.learn("server-a", [{"id": "team1.one"}])
        source.learn("server-b", [{"id": "team2.two"}])
        instance = package("team2.two")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mod.zip"
            source.download(instance, release(), release().assets[0], target)

        self.assertEqual(first.calls, [], "不该去问另一台服务器")
        self.assertEqual(len(second.calls), 1)
        self.assertEqual(second.calls[0][0], "team2.two")
        self.assertEqual(second.calls[0][3], release().assets[0].download_url,
                         "用条目里的绝对地址，且仍由 client 校验 origin")

    def test_the_latest_refresh_wins_the_ownership(self) -> None:
        source, first, second = self.source()
        source.learn("server-a", [{"id": "team1.shared"}])
        source.learn("server-b", [{"id": "team1.shared"}])

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mod.zip"
            source.download(package("team1.shared"), release(), release().assets[0], target)

        self.assertEqual(first.calls, [])
        self.assertEqual(len(second.calls), 1)

    def test_forgetting_a_server_drops_its_packages(self) -> None:
        source, _first, _second = self.source()
        source.learn("server-a", [{"id": "team1.one"}, {"id": "team1.two"}])
        source.learn("server-b", [{"id": "team2.three"}])

        source.forget("server-a")

        self.assertFalse(source.handles(package("team1.one")))
        self.assertFalse(source.handles(package("team1.two")))
        self.assertTrue(source.handles(package("team2.three")), "别的服务器不受牵连")

    def test_an_unowned_download_is_an_error_not_a_silent_skip(self) -> None:
        source, _first, _second = self.source()

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DownloadError, "no developer server"):
                source.download(
                    package("team1.nobody"), release(), release().assets[0],
                    Path(directory) / "mod.zip",
                )

    def test_a_server_error_becomes_a_download_error(self) -> None:
        source = PrivatePackageSource({"server-a": FakeClient(fail="download_denied")})
        source.learn("server-a", [{"id": "team1.one"}])

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DownloadError, "download_denied"):
                source.download(
                    package("team1.one"), release(), release().assets[0],
                    Path(directory) / "mod.zip",
                )

    def test_learning_from_an_unknown_server_is_refused(self) -> None:
        source, _first, _second = self.source()

        with self.assertRaisesRegex(DownloadError, "unknown developer server"):
            source.learn("server-z", [{"id": "team1.one"}])


class PrivateRefreshTests(unittest.TestCase):
    def test_refresh_pulls_every_server_and_learns_ownership(self) -> None:
        first = FakeClient(packages=[{"id": "team1.one"}])
        second = FakeClient(packages=[{"id": "team2.two"}])
        source = PrivatePackageSource({"server-a": first, "server-b": second})

        merged = source.refresh()

        self.assertEqual([item["id"] for item in merged], ["team1.one", "team2.two"])
        self.assertEqual(source.owner_of("team1.one"), "server-a")
        self.assertEqual(source.owner_of("team2.two"), "server-b")
        self.assertEqual((first.package_calls, second.package_calls), (1, 1))

    def test_a_failing_server_is_not_swallowed(self) -> None:
        """连不上和验签不过是两件事，但都不能被静默跳过 —— 那会变成一条降级的路。"""

        class Broken(FakeClient):
            def packages(self) -> list[dict]:
                raise ValueError("manifest signature verification failed")

        source = PrivatePackageSource({"server-a": Broken()})

        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            source.refresh()


if __name__ == "__main__":
    unittest.main()
