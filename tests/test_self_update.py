"""单文件自更新：选发布、验摘要、换壳替换，以及界面侧那两个接口的边界。

不联网、不真的替换可执行文件：HTTP 与「起进程 / 等解锁」都是注入的。
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.domain.errors import DownloadError  # noqa: E402
from sprocket_mod_manager.domain.models import ReleaseAsset  # noqa: E402
from sprocket_mod_manager.domain.semver import Version  # noqa: E402
from sprocket_mod_manager.infrastructure import self_update  # noqa: E402
from sprocket_mod_manager.infrastructure.github import RepositoryRelease  # noqa: E402

PAYLOAD = b"new manager build"
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def asset(name: str, *, url: str, size: int = 0, digest: str = "") -> ReleaseAsset:
    return ReleaseAsset(id=1, name=name, size=size, download_url=url, digest=digest or None)


def release(version: str = "0.6.0", *, assets: tuple[ReleaseAsset, ...] = (), notes: str = "") -> RepositoryRelease:
    return RepositoryRelease(
        tag=f"v{version}",
        version=Version.parse(version),
        page_url=f"https://github.com/furryaxw/sprocket-mods/releases/tag/v{version}",
        notes=notes,
        assets=assets,
    )


def executable_asset(*, digest: str = f"sha256:{PAYLOAD_SHA}") -> ReleaseAsset:
    return asset(
        self_update.MANAGER_EXE_NAME,
        url=f"https://github.com/furryaxw/sprocket-mods/releases/download/v0.6.0/{self_update.MANAGER_EXE_NAME}",
        size=len(PAYLOAD),
        digest=digest,
    )


class FakeHttp:
    """只实现自更新用到的那两下：下载资产、取校验和文本。"""

    def __init__(self, data: bytes = PAYLOAD, checksum: str = "") -> None:
        self.data = data
        self.checksum = checksum
        self.downloads: list[str] = []
        self.reads: list[str] = []

    def download(self, asset: ReleaseAsset, destination: Path, progress=None) -> Path:
        self.downloads.append(asset.download_url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.data)
        return destination

    def get_bytes(self, url: str, **kwargs) -> bytes:
        self.reads.append(url)
        if not self.checksum:
            raise DownloadError("no checksum asset")
        return self.checksum.encode("utf-8")


class UpdateSelectionTests(unittest.TestCase):
    def test_a_newer_release_with_the_executable_is_an_update(self) -> None:
        update = self_update.update_from_release(
            release("0.6.0", assets=(executable_asset(),), notes="修了几个 bug"),
            "0.5.1",
        )

        self.assertIsNotNone(update)
        self.assertEqual(update.version, "0.6.0")
        self.assertEqual(update.tag, "v0.6.0")
        self.assertEqual(update.notes, "修了几个 bug")
        self.assertEqual(update.size, len(PAYLOAD))
        self.assertIn(self_update.MANAGER_EXE_NAME, update.download_url)

    def test_the_running_version_is_not_an_update(self) -> None:
        self.assertIsNone(self_update.update_from_release(release("0.5.1", assets=(executable_asset(),)), "0.5.1"))
        self.assertIsNone(self_update.update_from_release(release("0.4.0", assets=(executable_asset(),)), "0.5.1"))

    def test_a_release_without_the_executable_is_not_an_update(self) -> None:
        """发布里没有那个 exe（例如只有源码包）就没法自更新，别把用户带去一个装不上的流程。"""
        only_checksum = asset(f"{self_update.MANAGER_EXE_NAME}.sha256", url="https://github.com/x/y")

        self.assertIsNone(self_update.update_from_release(release("0.6.0", assets=(only_checksum,)), "0.5.1"))

    def test_an_unparseable_running_version_is_not_an_update(self) -> None:
        self.assertIsNone(self_update.update_from_release(release("0.6.0", assets=(executable_asset(),)), "dev"))

    def test_find_update_asks_github_for_the_latest_release(self) -> None:
        class Github:
            def __init__(self) -> None:
                self.repositories: list[str] = []

            def latest_repository_release(self, repository: str) -> RepositoryRelease:
                self.repositories.append(repository)
                return release("0.6.0", assets=(executable_asset(),))

        github = Github()
        update = self_update.find_update(github, "0.5.1")

        self.assertEqual(github.repositories, [self_update.MANAGER_REPOSITORY])
        self.assertEqual(update.version, "0.6.0")

    def test_the_checksum_asset_is_remembered_for_verification(self) -> None:
        update = self_update.update_from_release(
            release("0.6.0", assets=(executable_asset(), asset(
                f"{self_update.MANAGER_EXE_NAME}.sha256",
                url="https://github.com/furryaxw/sprocket-mods/releases/download/v0.6.0/x.sha256",
            ))),
            "0.5.1",
        )

        self.assertTrue(update.checksum_url.endswith("x.sha256"))


class DownloadTests(unittest.TestCase):
    def test_a_matching_digest_is_accepted(self) -> None:
        http = FakeHttp()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / self_update.MANAGER_EXE_NAME
            staged = self_update.download_update(
                http, self_update.update_from_release(release(assets=(executable_asset(),)), "0.5.1"), target
            )

            self.assertEqual(staged, target)
            self.assertEqual(target.read_bytes(), PAYLOAD)
            self.assertEqual(list(target.parent.glob("*.part")), [], "下载的临时文件不该留下")

    def test_a_mismatched_digest_raises_and_leaves_nothing_behind(self) -> None:
        http = FakeHttp(b"tampered")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / self_update.MANAGER_EXE_NAME
            update = self_update.update_from_release(release(assets=(executable_asset(),)), "0.5.1")

            with self.assertRaisesRegex(DownloadError, "SHA-256"):
                self_update.download_update(http, update, target)

            self.assertFalse(target.exists(), "校验没过就不能留下可执行的新版本")
            self.assertEqual(list(target.parent.iterdir()), [])

    def test_the_checksum_asset_is_used_when_the_asset_carries_no_digest(self) -> None:
        http = FakeHttp(checksum=f"{PAYLOAD_SHA}  {self_update.MANAGER_EXE_NAME}\n")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / self_update.MANAGER_EXE_NAME
            update = self_update.update_from_release(
                release(assets=(
                    executable_asset(digest=""),
                    asset(f"{self_update.MANAGER_EXE_NAME}.sha256", url="https://github.com/a/b/x.sha256"),
                )),
                "0.5.1",
            )

            self_update.download_update(http, update, target)

            self.assertEqual(http.reads, ["https://github.com/a/b/x.sha256"])
            self.assertTrue(target.is_file())

    def test_a_checksum_asset_that_does_not_match_still_fails(self) -> None:
        http = FakeHttp(checksum="0" * 64 + f"  {self_update.MANAGER_EXE_NAME}\n")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / self_update.MANAGER_EXE_NAME
            update = self_update.update_from_release(
                release(assets=(
                    executable_asset(digest=""),
                    asset(f"{self_update.MANAGER_EXE_NAME}.sha256", url="https://github.com/a/b/x.sha256"),
                )),
                "0.5.1",
            )

            with self.assertRaises(DownloadError):
                self_update.download_update(http, update, target)


class SelfUpdateModeTests(unittest.TestCase):
    def test_the_child_replaces_the_target_and_restarts_it(self) -> None:
        launched: list[list[str]] = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "SprocketModManager.exe"
            staged = Path(directory) / "SprocketModManager.new.exe"
            target.write_bytes(b"old")
            staged.write_bytes(b"new")

            code = self_update.self_update_mode(
                [self_update.SELF_UPDATE_FLAG, str(target), str(staged)],
                wait=lambda _path, _timeout: True,
                launch=launched.append,
            )

            self.assertEqual(code, 0)
            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual(launched, [[str(target)]])
            self.assertFalse(
                Path(f"{target}{self_update.DOWNLOAD_PART_SUFFIX}").exists(),
                "换名之后不该留下中转文件",
            )

    def test_a_target_that_stays_locked_is_left_alone(self) -> None:
        launched: list[list[str]] = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "SprocketModManager.exe"
            staged = Path(directory) / "SprocketModManager.new.exe"
            target.write_bytes(b"old")
            staged.write_bytes(b"new")

            code = self_update.self_update_mode(
                [self_update.SELF_UPDATE_FLAG, str(target), str(staged)],
                wait=lambda _path, _timeout: False,
                launch=launched.append,
            )

            self.assertEqual(code, 1)
            self.assertEqual(target.read_bytes(), b"old", "旧版本还在跑，不能动它")
            self.assertEqual(launched, [])

    def test_the_child_needs_both_paths(self) -> None:
        self.assertEqual(self_update.self_update_mode([self_update.SELF_UPDATE_FLAG]), 2)

    def test_the_child_refuses_to_replace_itself(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            same = Path(directory) / "SprocketModManager.new.exe"
            same.write_bytes(b"new")

            self.assertEqual(
                self_update.self_update_mode(
                    [self_update.SELF_UPDATE_FLAG, str(same), str(same)],
                    wait=lambda _path, _timeout: True,
                ),
                1,
            )

    @patch.object(self_update, "start_process")
    def test_launch_hands_the_child_its_own_path_and_the_running_one(self, start_process) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "SprocketModManager.exe"
            staged = Path(directory) / "SprocketModManager.new.exe"
            app_dir = Path(directory) / "app"

            self_update.launch_self_update(current, staged, app_dir=app_dir)

            argv = start_process.call_args[0][0]
            self.assertEqual(argv[:4], [str(staged), self_update.SELF_UPDATE_FLAG, str(current), str(staged)])
            self.assertEqual(argv[4:], ["--app-dir", str(app_dir)])

    def test_launch_refuses_a_staged_file_that_is_the_running_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "SprocketModManager.exe"

            with self.assertRaises(ValueError):
                self_update.launch_self_update(current, current)


class StagedFileTests(unittest.TestCase):
    def test_the_staged_name_sits_next_to_the_running_executable(self) -> None:
        current = Path("C:/games/tools/SprocketModManager.exe")

        self.assertEqual(
            self_update.staged_executable(current).name, "SprocketModManager.new.exe"
        )
        self.assertEqual(self_update.staged_executable(current).parent, current.parent)

    def test_cleanup_removes_the_previous_round(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "SprocketModManager.exe"
            current.write_bytes(b"self")
            leftovers = [path for path in self_update.staged_files(current)]
            for path in leftovers[1:]:
                path.write_bytes(b"leftover")
            leftovers[0].write_bytes(b"staged")

            self_update.cleanup_staged(current)

            self.assertTrue(current.is_file(), "自己当然要留着")
            for path in leftovers:
                self.assertFalse(path.exists())

    def test_cleanup_survives_a_locked_leftover(self) -> None:
        """上一轮的新版本可能还开着（换壳那一瞬间）：删不掉就算了，不能因此起不来。"""
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "SprocketModManager.exe"
            staged = self_update.staged_executable(current)
            staged.write_bytes(b"staged")

            with patch.object(Path, "unlink", side_effect=PermissionError("in use")):
                self_update.cleanup_staged(current)

            self.assertTrue(staged.exists())


class FrozenModeTests(unittest.TestCase):
    def test_a_source_run_cannot_self_update(self) -> None:
        with patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(self_update.frozen_executable())
            self.assertFalse(self_update.can_self_update())

    def test_a_packaged_build_points_at_its_own_executable(self) -> None:
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", r"C:\tools\SprocketModManager.exe"
        ):
            self.assertEqual(
                self_update.frozen_executable(), Path(r"C:\tools\SprocketModManager.exe")
            )
            self.assertTrue(self_update.can_self_update())


if __name__ == "__main__":
    unittest.main()
