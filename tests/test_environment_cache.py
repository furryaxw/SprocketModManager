"""环境表的同步缓存：写、读、坏文件、原子写。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.infrastructure.environment_cache import (
    environment_cache_path,
    read_environment_table,
    write_environment_table,
)

TABLE = {"schema_version": 1, "entries": [{"melonloader": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}]}


class EnvironmentCacheTests(unittest.TestCase):
    def test_a_written_table_reads_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(write_environment_table(directory, TABLE))

            table = read_environment_table(directory)

        self.assertEqual(table, TABLE)

    def test_nothing_synced_yet_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(read_environment_table(directory))

    def test_a_corrupted_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = environment_cache_path(directory)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ not json", encoding="utf-8")

            self.assertIsNone(read_environment_table(directory))

    def test_a_table_without_entries_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(write_environment_table(directory, TABLE))
            self.assertFalse(write_environment_table(directory, {"entries": []}))

            self.assertEqual(read_environment_table(directory), TABLE, "空表不代表平台事实被撤销")

    def test_writing_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_environment_table(directory, TABLE)

            leftovers = [path.name for path in environment_cache_path(directory).parent.iterdir()]

        self.assertEqual(leftovers, ["environment.json"])

    def test_the_cache_sits_in_the_manager_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = environment_cache_path(directory)
            write_environment_table(directory, TABLE)

            stored = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(path, Path(directory) / "cache" / "environment.json")
        self.assertEqual(stored, TABLE)


if __name__ == "__main__":
    unittest.main()
