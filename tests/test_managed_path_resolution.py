"""统一解析 `.dll` / `.dll.disable`：只有一处知道"真实文件在哪"，调用方只给基本名。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.infrastructure.mod_toggle import (
    ModToggleError,
    actual_managed_path,
    actual_path_for,
    apply_enabled,
)


class ManagedPathResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.game = Path(self._temporary.name)
        (self.game / "Mods").mkdir()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _write(self, name: str, content: bytes = b"x") -> Path:
        path = self.game / "Mods" / name
        path.write_bytes(content)
        return path

    def test_actual_path_prefers_the_canonical_name(self) -> None:
        enabled = self._write("Alpha.dll")
        self._write("Alpha.dll.disable")
        self.assertEqual(actual_path_for(self.game / "Mods" / "Alpha.dll"), enabled,
                         "两个变体同时存在时以 `.dll` 为准")

    def test_actual_path_finds_the_disabled_variant(self) -> None:
        disabled = self._write("Beta.dll.disable")
        self.assertEqual(actual_path_for(self.game / "Mods" / "Beta.dll"), disabled)
        self.assertEqual(actual_managed_path(self.game, "Mods/Beta.dll"), disabled)

    def test_actual_path_is_none_when_the_file_is_gone(self) -> None:
        self.assertIsNone(actual_path_for(self.game / "Mods" / "Gamma.dll"))
        self.assertIsNone(actual_managed_path(self.game, "Mods/Gamma.dll"))

    def test_apply_enabled_takes_a_base_name_both_ways(self) -> None:
        self._write("Delta.dll")
        disabled = apply_enabled(self.game / "Mods" / "Delta.dll", False)
        self.assertEqual(disabled.name, "Delta.dll.disable")
        self.assertTrue(disabled.is_file())

        enabled = apply_enabled(self.game / "Mods" / "Delta.dll", True)
        self.assertEqual(enabled.name, "Delta.dll", "再给基本名也能启用回来")
        self.assertTrue(enabled.is_file())

    def test_apply_enabled_is_idempotent(self) -> None:
        self._write("Epsilon.dll")
        first = apply_enabled(self.game / "Mods" / "Epsilon.dll", False)
        second = apply_enabled(self.game / "Mods" / "Epsilon.dll", False)
        self.assertEqual(first, second, "重复禁用不报错也不改名")
        self.assertEqual(apply_enabled(self.game / "Mods" / "Epsilon.dll", True).name, "Epsilon.dll")

    def test_apply_enabled_reports_missing_files(self) -> None:
        with self.assertRaises(ModToggleError):
            apply_enabled(self.game / "Mods" / "Missing.dll", False)


if __name__ == "__main__":
    unittest.main()
