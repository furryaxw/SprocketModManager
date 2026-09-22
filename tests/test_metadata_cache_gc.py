"""`file-metadata.json` 不能无限增长：清掉消失的路径、清掉没人引用的 hash。

缓存的结构是 `files{绝对路径: {size, mtime, hash}}` + `meta{hash: 解析结果}`：
真正有价值的是"路径 → hash"这条绑定。文件删了，绑定就没了；绑定没了，hash 的解析结果也没人要。
`flush_metadata_cache()` 每次写盘前都会清理一次（扫描结束时就会走到），所以大小只跟着**当前存在
的受管文件**走。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.infrastructure import dll_metadata  # noqa: E402
from sprocket_mod_manager.infrastructure.file_metadata import FileMetadataStore  # noqa: E402


class MetadataCacheGcTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.game = self.root / "game"
        (self.game / "Mods").mkdir(parents=True)
        self.existing = self.game / "Mods" / "Present.dll"
        self.existing.write_bytes(b"present")
        self.cache_path = self.game / "SprocketModManager" / "file-metadata.json"
        dll_metadata.clear_metadata_cache(include_disk=True)
        dll_metadata.configure_metadata_backend(FileMetadataStore(self.cache_path))

    def tearDown(self) -> None:
        dll_metadata.clear_metadata_cache(include_disk=True)
        dll_metadata.configure_metadata_backend(None)
        dll_metadata.configure_metadata_cache(None)
        self._directory.cleanup()

    def _seed(self, payload: dict) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        dll_metadata.clear_metadata_cache(include_disk=True)
        dll_metadata.configure_metadata_backend(FileMetadataStore(self.cache_path))
        # 让下一次 flush 真的写盘（把 sections 读进来，并标记为脏）
        dll_metadata._load_sections()  # noqa: SLF001 - 测试要用它把种子读进内存
        dll_metadata._sections_dirty = True  # noqa: SLF001

    def test_drops_vanished_paths_and_unreferenced_hashes(self) -> None:
        gone = self.game / "Mods" / "Gone.dll"
        self._seed({
            "schema_version": 1,
            "files": {
                str(self.existing): {"size": 7, "mtime": 1, "hash": "a" * 64},
                str(gone): {"size": 7, "mtime": 1, "hash": "b" * 64},
            },
            "meta": {
                "a" * 64: {"is_managed": True},
                "b" * 64: {"is_managed": True},
                "c" * 64: {"is_managed": True},   # 没有任何文件引用
            },
        })

        self.assertTrue(dll_metadata.flush_metadata_cache(), "清理后应该写盘")

        stored = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(list(stored["files"]), [str(self.existing)], "消失的路径要被清掉")
        self.assertEqual(list(stored["meta"]), ["a" * 64], "只剩被现存文件引用的 hash")

    def test_keeps_everything_while_the_files_are_still_there(self) -> None:
        second = self.game / "Mods" / "Second.dll"
        second.write_bytes(b"second")
        self._seed({
            "schema_version": 1,
            "files": {
                str(self.existing): {"size": 7, "mtime": 1, "hash": "a" * 64},
                str(second): {"size": 6, "mtime": 2, "hash": "b" * 64},
            },
            "meta": {"a" * 64: {"is_managed": True}, "b" * 64: {"is_managed": False}},
        })

        self.assertEqual(dll_metadata.prune_metadata_cache(), {"files": 0, "meta": 0},
                         "没有可清理的东西时一条也不许动")
        dll_metadata.flush_metadata_cache()

        stored = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(stored["files"]), sorted([str(self.existing), str(second)]))
        self.assertEqual(sorted(stored["meta"]), ["a" * 64, "b" * 64])

    def test_prune_returns_what_it_dropped(self) -> None:
        gone = self.game / "Mods" / "Gone.dll"
        self._seed({
            "schema_version": 1,
            "files": {
                str(self.existing): {"size": 7, "mtime": 1, "hash": "a" * 64},
                str(gone): {"size": 7, "mtime": 1, "hash": "b" * 64},
            },
            "meta": {"a" * 64: {}, "b" * 64: {}, "c" * 64: {}},
        })

        dropped = dll_metadata.prune_metadata_cache()
        self.assertEqual(dropped, {"files": 1, "meta": 2})


if __name__ == "__main__":
    unittest.main()
