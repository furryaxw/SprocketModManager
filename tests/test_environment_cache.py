"""供给表的同步缓存：写、读、坏文件、原子写。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.infrastructure.providers_cache import (
    providers_cache_path,
    read_providers_table,
    write_providers_table,
)

TABLE = {
    "schema_version": 2,
    "entries": [{"loader": "lavagang.melonloader", "version": ">=0.7.0 <0.8.0", "sprocket": "<0.2.54.0"}],
}


class ProvidersCacheTests(unittest.TestCase):
    def test_a_written_table_reads_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(write_providers_table(directory, TABLE))

            table = read_providers_table(directory)

        self.assertEqual(table, TABLE)

    def test_nothing_synced_yet_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(read_providers_table(directory))

    def test_a_corrupted_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = providers_cache_path(directory)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ not json", encoding="utf-8")

            self.assertIsNone(read_providers_table(directory))

    def test_a_table_without_entries_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(write_providers_table(directory, TABLE))
            self.assertFalse(write_providers_table(directory, {"entries": []}))

            self.assertEqual(read_providers_table(directory), TABLE, "空表不代表平台事实被撤销")

    def test_writing_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_providers_table(directory, TABLE)

            leftovers = [path.name for path in providers_cache_path(directory).parent.iterdir()]

        self.assertEqual(leftovers, ["providers.json"])

    def test_the_cache_sits_in_the_manager_cache_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = providers_cache_path(directory)
            write_providers_table(directory, TABLE)

            stored = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(path, Path(directory) / "cache" / "providers.json")
        self.assertEqual(stored, TABLE)


if __name__ == "__main__":
    unittest.main()
