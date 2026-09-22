from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sprocket_mod_manager.domain.errors import ModToggleError
from sprocket_mod_manager.infrastructure.mod_toggle import (
    disabled_path_for,
    enabled_path_for,
    find_disabled_dlls,
    is_disabled_path,
    is_loadable_path,
    resolve_mod_path,
    set_enabled,
)


class PathHelpersTests(unittest.TestCase):
    def test_suffix_helpers(self) -> None:
        self.assertTrue(is_loadable_path(Path("Mods/Example.dll")))
        self.assertFalse(is_loadable_path(Path("Mods/Example.dll.disable")))
        self.assertTrue(is_disabled_path(Path("Mods/Example.dll.disable")))
        self.assertFalse(is_disabled_path(Path("Mods/Example.dll")))
        self.assertTrue(is_loadable_path(Path("Mods/Example.DLL")))
        self.assertTrue(is_disabled_path(Path("Mods/Example.DLL.DISABLE")))

    def test_rename_targets(self) -> None:
        path = Path("Mods/Example.dll")
        self.assertEqual(disabled_path_for(path).name, "Example.dll.disable")
        self.assertEqual(enabled_path_for(disabled_path_for(path)), path)
        self.assertEqual(enabled_path_for(path), path)


class SetEnabledTests(unittest.TestCase):
    def test_disable_and_enable_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mod = Path(directory) / "Example.dll"
            mod.write_bytes(b"content")

            disabled = set_enabled(mod, False)
            self.assertEqual(disabled.name, "Example.dll.disable")
            self.assertFalse(mod.exists())
            self.assertTrue(disabled.is_file())

            enabled = set_enabled(disabled, True)
            self.assertEqual(enabled.name, "Example.dll")
            self.assertTrue(mod.is_file())
            self.assertFalse(disabled.exists())

    def test_wrong_kind_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mod = Path(directory) / "Example.dll"
            mod.write_bytes(b"content")
            with self.assertRaises(ModToggleError):
                set_enabled(mod, True)

            disabled = set_enabled(mod, False)
            with self.assertRaises(ModToggleError):
                set_enabled(disabled, False)

    def test_missing_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ModToggleError):
                set_enabled(Path(directory) / "Missing.dll", False)
            with self.assertRaises(ModToggleError):
                set_enabled(Path(directory) / "Missing.dll.disable", True)

    def test_existing_target_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mod = Path(directory) / "Example.dll"
            mod.write_bytes(b"content")
            (Path(directory) / "Example.dll.disable").write_bytes(b"other")

            with self.assertRaises(ModToggleError):
                set_enabled(mod, False)

            self.assertTrue(mod.is_file())
            self.assertEqual((Path(directory) / "Example.dll.disable").read_bytes(), b"other")

    def test_find_disabled_dlls_takes_one_level(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "a.dll.disable").write_bytes(b"a")
            (root / "nested" / "b.dll.disable").write_bytes(b"b")
            (root / "c.dll").write_bytes(b"c")

            found = find_disabled_dlls(root)
            self.assertEqual([path.name for path in found], ["a.dll.disable"])

    def test_find_disabled_dlls_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(find_disabled_dlls(Path(directory) / "nope"), [])


class ResolveModPathTests(unittest.TestCase):
    GAME = Path(r"C:\Game")

    def test_accepts_standard_roots(self) -> None:
        for relative in ("Mods/Example.dll", "Plugins/Tool.dll", "Mods/Example.dll.disable"):
            self.assertEqual(resolve_mod_path(self.GAME, relative), self.GAME / relative)

    def test_accepts_backslashes(self) -> None:
        self.assertEqual(resolve_mod_path(self.GAME, r"Mods\Example.dll"), self.GAME / "Mods" / "Example.dll")

    def test_rejects_unsafe_or_unsupported_paths(self) -> None:
        candidates = (
            "",
            "   ",
            r"C:\Windows\System32\x.dll",
            "../outside.dll",
            "Mods/../outside.dll",
            "UserData/thing.dll",
            "UserLibs/Lib.dll",
            "Mods/thing.txt",
            "Mods/nested/../../outside.dll",
        )
        for candidate in candidates:
            with self.assertRaises(ModToggleError, msg=repr(candidate)):
                resolve_mod_path(self.GAME, candidate)


if __name__ == "__main__":
    unittest.main()
