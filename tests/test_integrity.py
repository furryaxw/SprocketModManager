"""完整性判定的离线契约：命中发布版本 / 无发布数据 / 损坏 / 读不出来。

判定是**实时派生**的（`application/integrity.py`），不写进安装记录，所以这里全部是纯函数测试：
给一份状态视图 + 一份发布版本 hash 表 + 一个磁盘 hash 提供者，断言算出来的状态。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.application.integrity import (
    STATUS_CORRUPTED,
    STATUS_LOCAL,
    STATUS_RELEASE,
    STATUS_UNREADABLE,
    annotate,
    classify,
    package_status,
    published_hashes,
)
from sprocket_mod_manager.domain.models import RegistryPackage
from sprocket_mod_manager.infrastructure.dll_metadata import cached_sha256

PACKAGE_ID = "fixture.sprocket-mod"
V1_HASH = "a" * 64
V2_HASH = "b" * 64
FOREIGN_HASH = "c" * 64


def build_package(package_id: str = PACKAGE_ID, hashes: tuple[tuple[str, str], ...] = (("1.0.0", V1_HASH),)) -> RegistryPackage:
    releases = [
        {
            "id": index,
            "tag": f"v{version}",
            "version": version,
            "prerelease": False,
            "published_at": "2026-01-01T00:00:00Z",
            "page_url": f"https://github.com/example/Repo/releases/tag/v{version}",
            "assets": [
                {
                    "id": index,
                    "name": "Fixture.dll",
                    "size": 1024,
                    "download_url": f"https://github.com/example/Repo/releases/download/v{version}/Fixture.dll",
                    "digest": f"sha256:{digest}",
                }
            ],
        }
        for index, (version, digest) in enumerate(hashes, start=1)
    ]
    return RegistryPackage.from_dict(
        {
            "id": package_id,
            "name": "FixtureMod",
            "repository": "example/Repo",
            "release": {
                "version_pattern": r"^v?([0-9]+\.[0-9]+\.[0-9]+)$",
                "assets": {"include": ["Fixture.dll"], "exclude": []},
            },
            "releases": releases,
        }
    )


class PublishedHashesTests(unittest.TestCase):
    def test_keeps_every_published_release(self) -> None:
        table = published_hashes([build_package(hashes=(("2.0.0", V2_HASH), ("1.0.0", V1_HASH)))])
        self.assertEqual(table[PACKAGE_ID]["fixture.dll"][V1_HASH], "1.0.0")
        self.assertEqual(table[PACKAGE_ID]["fixture.dll"][V2_HASH], "2.0.0")

    def test_package_without_releases_contributes_nothing(self) -> None:
        package = build_package()
        object.__setattr__(package, "releases", None)
        self.assertEqual(published_hashes([package]), {})

    def test_missing_registry_is_empty_not_an_error(self) -> None:
        self.assertEqual(published_hashes(None), {})


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        table = published_hashes([build_package(hashes=(("2.0.0", V2_HASH), ("1.0.0", V1_HASH)))])
        self.published = table[PACKAGE_ID]["fixture.dll"]

    def test_matching_any_published_release_is_healthy(self) -> None:
        self.assertEqual(classify(V1_HASH, self.published), (STATUS_RELEASE, "1.0.0"))
        self.assertEqual(classify(V2_HASH.upper(), self.published), (STATUS_RELEASE, "2.0.0"))

    def test_file_matching_no_release_is_corrupted(self) -> None:
        """本地自行构建的 DLL 也照此报红 。"""
        self.assertEqual(classify(FOREIGN_HASH, self.published), (STATUS_CORRUPTED, ""))

    def test_without_published_data_nothing_is_corrupted(self) -> None:
        self.assertEqual(classify(FOREIGN_HASH, {}), (STATUS_LOCAL, ""))
        self.assertEqual(classify(FOREIGN_HASH, None), (STATUS_LOCAL, ""))

    def test_unreadable_file_is_broken(self) -> None:
        self.assertEqual(classify(None, self.published), (STATUS_UNREADABLE, ""))


class PackageStatusTests(unittest.TestCase):
    def test_any_broken_file_makes_the_package_broken(self) -> None:
        self.assertEqual(package_status([STATUS_RELEASE, STATUS_CORRUPTED]), STATUS_CORRUPTED)
        self.assertEqual(package_status([STATUS_UNREADABLE]), STATUS_CORRUPTED)

    def test_no_release_match_is_local(self) -> None:
        self.assertEqual(package_status([STATUS_LOCAL, STATUS_LOCAL]), STATUS_LOCAL)
        self.assertEqual(package_status([]), STATUS_LOCAL)


class DisabledFileTests(unittest.TestCase):
    """禁用是**改名**（`X.dll` → `X.dll.disable`），判定必须照到真实的那份文件上。

    拿状态里的规范路径直接去算 hash，禁用中的文件会"读不到" → `unreadable` →
    整个包被判 `corrupted`，所以 `annotate` 按"规范名 → `.dll.disable`"的顺序找真实文件。
    """

    def _state(self) -> dict:
        return {
            "files": {"Mods/Disabled.dll": {"sha256": "a" * 64, "disabled": True, "owners": [PACKAGE_ID]}},
            "packages": {PACKAGE_ID: {"name": "Disabled", "version": "1.0.0",
                                      "files": ["Mods/Disabled.dll"], "dependencies": []}},
        }

    def _game_dir(self, directory: str, content: bytes) -> Path:
        import hashlib

        game = Path(directory)
        (game / "Mods").mkdir(parents=True)
        disabled = game / "Mods" / "Disabled.dll.disable"
        disabled.write_bytes(content)
        return game

    def test_disabled_file_is_hashed_through_its_disable_name(self) -> None:
        import hashlib

        with tempfile.TemporaryDirectory() as directory:
            game = self._game_dir(directory, b"content")
            digest = hashlib.sha256(b"content").hexdigest()
            state = self._state()
            annotate(
                state,
                game_dir=game,
                published_by_package={PACKAGE_ID: {"disabled.dll": {digest: "1.0.0"}}},
                hash_provider=cached_sha256,
            )

        entry = state["files"]["Mods/Disabled.dll"]
        self.assertEqual(entry["disk_sha256"], digest, "要照到 .dll.disable 那份文件")
        self.assertEqual(entry["integrity"], STATUS_RELEASE, "禁用不该改变判定结果")
        self.assertFalse(state["packages"][PACKAGE_ID]["corrupted"])

    def test_disabled_file_without_published_data_stays_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = self._game_dir(directory, b"content")
            state = self._state()
            annotate(
                state,
                game_dir=game,
                published_by_package={},
                hash_provider=cached_sha256,
            )

        self.assertEqual(state["files"]["Mods/Disabled.dll"]["integrity"], STATUS_LOCAL)
        self.assertFalse(state["packages"][PACKAGE_ID]["corrupted"],
                         "没有可比发布数据时禁用不许报损坏")


class AnnotateTests(unittest.TestCase):
    def _state(self) -> dict:
        return {
            "files": {
                "Mods/FixtureMod.dll": {
                    "sha256": V1_HASH,
                    "disabled": False,
                    "owners": [PACKAGE_ID],
                },
                "Mods/GhostMod.dll": {
                    "sha256": "d" * 64,
                    "disabled": False,
                    "owners": [],
                },
            },
            "packages": {
                PACKAGE_ID: {
                    "name": "FixtureMod",
                    "version": "1.0.0",
                    "files": ["Mods/FixtureMod.dll"],
                },
                "local.only": {
                    "name": "LocalMod",
                    "version": "0.0.1",
                    "files": ["Mods/GhostMod.dll"],
                },
            },
        }

    def test_marks_files_and_packages_without_touching_disk(self) -> None:
        state = self._state()
        with tempfile.TemporaryDirectory() as directory:
            annotate(
                state,
                game_dir=Path(directory),
                published_by_package={PACKAGE_ID: {"fixturemod.dll": {V1_HASH: "1.0.0"}}},
                hash_provider=lambda path: V1_HASH if path.name == "FixtureMod.dll" else FOREIGN_HASH,
            )

        fixture = state["files"]["Mods/FixtureMod.dll"]
        self.assertEqual(fixture["integrity"], STATUS_RELEASE)
        self.assertEqual(fixture["matched_version"], "1.0.0")
        self.assertEqual(fixture["disk_sha256"], V1_HASH)
        ghost = state["files"]["Mods/GhostMod.dll"]
        self.assertEqual(ghost["integrity"], STATUS_LOCAL, "没有任何发布数据的包不算损坏")
        self.assertTrue(state["packages"][PACKAGE_ID]["corrupted"] is False)
        self.assertEqual(state["packages"][PACKAGE_ID]["integrity"], STATUS_RELEASE)

    def test_tampered_file_marks_the_package_corrupted(self) -> None:
        state = self._state()
        with tempfile.TemporaryDirectory() as directory:
            annotate(
                state,
                game_dir=Path(directory),
                published_by_package={PACKAGE_ID: {"fixturemod.dll": {V1_HASH: "1.0.0"}}},
                hash_provider=lambda _path: FOREIGN_HASH,
            )

        self.assertEqual(state["files"]["Mods/FixtureMod.dll"]["integrity"], STATUS_CORRUPTED)
        self.assertTrue(state["packages"][PACKAGE_ID]["corrupted"])

    def test_a_fileless_modloader_is_not_corrupted(self) -> None:
        """基础运行时不记逐文件清单：没有可比的磁盘文件，判定是 `local` 而不是损坏。"""
        loader_id = "lavagang.melonloader"
        state = {
            "files": {},
            "packages": {
                loader_id: {
                    "kind": "modloader",
                    "name": "MelonLoader",
                    "version": "0.7.3",
                    "files": [],
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            annotate(
                state,
                game_dir=Path(directory),
                published_by_package={loader_id: {"melonloader.x64.zip": {V1_HASH: "0.7.3"}}},
                hash_provider=lambda _path: None,
            )

        loader = state["packages"][loader_id]
        self.assertEqual(loader["integrity"], STATUS_LOCAL)
        self.assertFalse(loader["corrupted"])

    def test_archive_only_package_falls_back_to_local_not_corrupted(self) -> None:
        """有的包发布的是压缩包（UnityExplorer 的 .zip）：解压出来的 DLL 无可比数据 → `local`。"""
        state = self._state()
        with tempfile.TemporaryDirectory() as directory:
            annotate(
                state,
                game_dir=Path(directory),
                published_by_package={PACKAGE_ID: {"fixture.dll.zip": {V1_HASH: "1.0.0"}}},
                hash_provider=lambda _path: FOREIGN_HASH,
            )

        self.assertEqual(state["files"]["Mods/FixtureMod.dll"]["integrity"], STATUS_LOCAL)
        self.assertFalse(state["packages"][PACKAGE_ID]["corrupted"])

    def test_precomputed_hashes_win_over_the_provider(self) -> None:
        """`verify` 刚强制算过一遍：判定必须用那份结果，别再走 (size, mtime) 快路径。"""
        state = self._state()
        calls: list[Path] = []

        def provider(path: Path) -> str:
            calls.append(path)
            return "f" * 64

        with tempfile.TemporaryDirectory() as directory:
            annotate(
                state,
                game_dir=Path(directory),
                published_by_package={PACKAGE_ID: {"fixturemod.dll": {V1_HASH: "1.0.0"}}},
                hash_provider=provider,
                hashes={"Mods/FixtureMod.dll": V1_HASH},
            )

        self.assertEqual(state["files"]["Mods/FixtureMod.dll"]["integrity"], STATUS_RELEASE)
        self.assertEqual(len(calls), 1, "只有没给 hash 的那个文件才需要问提供者")


if __name__ == "__main__":
    unittest.main()
