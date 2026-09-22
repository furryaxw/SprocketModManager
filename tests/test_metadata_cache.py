"""磁盘元数据缓存：第二次以后打开管理器不该再花两秒解析同一批 DLL。

测试在临时目录里拷贝夹具，绝不改动仓库里的夹具 DLL。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dll_metadata" / "dll"
FIXTURE_MOD = FIXTURE_DIR / "FixtureMod.dll"
FIXTURE_PLUGIN = FIXTURE_DIR / "FixturePlugin.dll"


class DiskMetadataCacheTests(unittest.TestCase):
    def test_second_run_reads_from_disk_instead_of_parsing(self) -> None:
        from sprocket_mod_manager.infrastructure import dll_metadata as module

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "FixtureMod.dll"
            shutil.copyfile(FIXTURE_MOD, target)
            cache_path = root / "metadata-cache.json"
            module.configure_metadata_cache(cache_path)

            calls: list[str] = []
            real_read = module.read_dll_metadata

            def counting_read(path):
                calls.append(str(path))
                return real_read(path)

            try:
                with patch.object(module, "read_dll_metadata", counting_read):
                    first = module.read_cached_metadata(target)
                    self.assertEqual(len(calls), 1, "first read parses the DLL")
                    self.assertTrue(module.flush_metadata_cache(), "the parsed entry is written to disk")
                    self.assertTrue(cache_path.is_file())

                    # 只清内存，模拟"下次启动"：结果必须来自磁盘缓存。
                    module.clear_metadata_cache()
                    second = module.read_cached_metadata(target)
                    self.assertEqual(len(calls), 1, "the warm disk cache must not re-parse")
                    self.assertEqual(second.melon_name, first.melon_name)
                    self.assertEqual(second.sprocket, first.sprocket)
                    self.assertEqual(second.required_dependencies, first.required_dependencies)
                    self.assertIsInstance(second.errors, tuple, "tuple fields keep their runtime shape")

                    # 文件变了（内容与 mtime）就必须重新解析。
                    shutil.copyfile(FIXTURE_PLUGIN, target)
                    module.clear_metadata_cache()
                    third = module.read_cached_metadata(target)
                    self.assertEqual(len(calls), 2, "a changed file is re-parsed")
                    self.assertEqual(third.melon_name, "Fixture Plugin")
            finally:
                module.configure_metadata_cache(None)
                module.clear_metadata_cache(include_disk=True)

    def test_scan_persists_the_cache_without_an_explicit_flush(self) -> None:
        """扫描自己必须落盘缓存：命令行每次从零解析同一批 DLL 要花约 2.8 s。"""
        from sprocket_mod_manager.application.local_mods import scan_local_mods
        from sprocket_mod_manager.infrastructure import dll_metadata as module

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            (game / "Mods").mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            shutil.copyfile(FIXTURE_MOD, game / "Mods" / "FixtureMod.dll")
            cache_path = root / "metadata-cache.json"
            module.configure_metadata_cache(cache_path)

            calls: list[str] = []
            real_read = module.read_dll_metadata

            def counting_read(path):
                calls.append(str(path))
                return real_read(path)

            try:
                with patch.object(module, "read_dll_metadata", counting_read):
                    scan_local_mods(game, {}, ())
                    self.assertEqual(len(calls), 1, "the first scan parses the DLL")
                    self.assertTrue(cache_path.is_file(), "the scan itself must write the cache to disk")

                    # 模拟"下次启动"：重新配置缓存 = 丢掉内存缓存与已加载标记，只留缓存文件。
                    module.configure_metadata_cache(cache_path)
                    scan_local_mods(game, {}, ())
                    self.assertEqual(len(calls), 1, "the next scan must be served by the disk cache")
            finally:
                module.configure_metadata_cache(None)
                module.clear_metadata_cache(include_disk=True)

    def test_missing_or_corrupt_cache_is_not_fatal(self) -> None:
        from sprocket_mod_manager.infrastructure import dll_metadata as module

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "FixtureMod.dll"
            shutil.copyfile(FIXTURE_MOD, target)
            cache_path = root / "metadata-cache.json"
            cache_path.write_text("{ not json", encoding="utf-8")
            module.configure_metadata_cache(cache_path)
            try:
                metadata = module.read_cached_metadata(target)
                self.assertEqual(metadata.melon_name, "Fixture Mod")
            finally:
                module.configure_metadata_cache(None)
                module.clear_metadata_cache(include_disk=True)

    def test_disk_cache_is_off_by_default(self) -> None:
        from sprocket_mod_manager.infrastructure import dll_metadata as module

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "FixtureMod.dll"
            shutil.copyfile(FIXTURE_MOD, target)
            module.configure_metadata_cache(None)
            module.clear_metadata_cache(include_disk=True)
            try:
                self.assertEqual(module.read_cached_metadata(target).melon_name, "Fixture Mod")
                self.assertFalse(module.flush_metadata_cache(), "no disk cache means nothing to flush")
            finally:
                module.clear_metadata_cache(include_disk=True)


if __name__ == "__main__":
    unittest.main()
