"""缓存清理：三个入口各删各的，删除的数量与字节要如实报回。

清理只删「能再取回来的东西」：安装记录、备份、配置都不在这些目录里，所以这里同时钉住
「没有多删」那一半。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.infrastructure import dll_metadata
from sprocket_mod_manager.infrastructure.app_cache import (
    app_cache_dir,
    clear_app_cache,
    clear_file_metadata_cache,
    clear_http_cache,
    http_cache_dir,
)
from sprocket_mod_manager.infrastructure.diagnosis_cache import diagnosis_cache_path
from sprocket_mod_manager.infrastructure.file_metadata import FileMetadataStore
from sprocket_mod_manager.infrastructure.manager_paths import file_metadata_path, state_file_path
from sprocket_mod_manager.infrastructure.providers_cache import providers_cache_path, write_providers_table

TABLE = {
    "schema_version": 2,
    "entries": [{"loader": "lavagang.melonloader", "version": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}],
}


class AppCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.app_dir = self.root / "app"

    def test_the_cache_layout_sits_under_the_app_directory(self) -> None:
        self.assertEqual(app_cache_dir(self.app_dir), self.app_dir / "cache")
        self.assertEqual(http_cache_dir(self.app_dir), self.app_dir / "cache" / "http")

    def test_clearing_the_http_cache_keeps_the_synced_tables(self) -> None:
        self._seed_app_cache()
        self._write_cached_document(b"payload")

        removed = clear_http_cache(self.app_dir)

        self.assertEqual(removed.files, 1)
        self.assertEqual(removed.bytes_removed, len(b"payload"))
        self.assertFalse(http_cache_dir(self.app_dir).exists())
        self.assertTrue(providers_cache_path(self.app_dir).is_file(), "供给表不是网络文档缓存")
        self.assertTrue(diagnosis_cache_path(self.app_dir).is_file(), "规则包不是网络文档缓存")

    def test_clearing_everything_takes_the_whole_cache_directory(self) -> None:
        self._seed_app_cache()
        self._write_cached_document(b"payload")
        expected = self._cached_bytes()

        removed = clear_app_cache(self.app_dir)

        self.assertEqual(removed.files, 3)
        self.assertEqual(removed.bytes_removed, expected)
        self.assertFalse(app_cache_dir(self.app_dir).exists())

    def test_clearing_a_cache_that_is_not_there_reports_nothing(self) -> None:
        self.assertEqual(clear_http_cache(self.app_dir).files, 0)
        self.assertEqual(clear_app_cache(self.app_dir).bytes_removed, 0)

    def _seed_app_cache(self) -> None:
        write_providers_table(self.app_dir, TABLE)
        path = diagnosis_cache_path(self.app_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"entries": []}', encoding="utf-8")

    def _write_cached_document(self, payload: bytes) -> None:
        directory = http_cache_dir(self.app_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "abc.body").write_bytes(payload)

    def _cached_bytes(self) -> int:
        return sum(
            entry.stat().st_size
            for entry in app_cache_dir(self.app_dir).rglob("*")
            if entry.is_file()
        )


class FileMetadataCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.game = self.root / "game"
        self.addCleanup(dll_metadata.clear_metadata_cache, include_disk=True)
        self.addCleanup(dll_metadata.configure_metadata_backend, None)

    def test_clearing_the_metadata_cache_keeps_the_install_record(self) -> None:
        path = file_metadata_path(self.game)
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
        installed = state_file_path(self.game)
        installed.write_text("{}", encoding="utf-8")

        removed = clear_file_metadata_cache(self.game)

        self.assertEqual(removed.files, 1)
        self.assertEqual(removed.bytes_removed, len("{}"))
        self.assertFalse(path.exists())
        self.assertTrue(installed.is_file(), "安装记录不是缓存")

    def test_a_cleared_metadata_cache_does_not_come_back_from_memory(self) -> None:
        managed = self.game / "Mods" / "Present.dll"
        managed.parent.mkdir(parents=True)
        managed.write_bytes(b"present")
        path = file_metadata_path(self.game)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "schema_version": dll_metadata._CACHE_VERSION,  # noqa: SLF001
                "files": {str(managed): {"size": 7, "mtime": 1, "hash": "a" * 64}},
                "meta": {"a" * 64: {"is_managed": True}},
            }),
            encoding="utf-8",
        )
        dll_metadata.clear_metadata_cache(include_disk=True)
        dll_metadata.configure_metadata_backend(FileMetadataStore(path))
        dll_metadata._load_sections()  # noqa: SLF001 - 让内存里真的有这份种子

        clear_file_metadata_cache(self.game)

        self.assertFalse(path.exists())
        self.assertFalse(dll_metadata.flush_metadata_cache(), "内存那份不许把旧条目写回来")
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
