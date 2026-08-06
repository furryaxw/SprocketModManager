import tempfile
import unittest
import zipfile
from pathlib import Path

from sprocket_mod_manager.errors import ScanError
from sprocket_mod_manager.models import RegistryPackage
from sprocket_mod_manager.scanner import PackageScanner


def package(*, translation=False):
    return RegistryPackage(
        id="test.mod",
        name="TestMod",
        authors=("test",),
        repository="test/repo",
        license="MIT",
        display_name={"en": "Test"},
        description={"en": "Test"},
        release={},
        dependencies=(),
        install={
            "scan_dlls": not translation,
            "exclude": [],
            "overrides": [],
            **({"mode": "xunity-translation"} if translation else {}),
        },
        category="translation" if translation else "utility",
        tags=(),
    )


class ScannerTests(unittest.TestCase):
    def test_xunity_translation_zip_preserves_all_relative_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "zh_cn.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("Config.ini", b"language=zh-CN")
                output.writestr("Translation/zh-CN/Text/Translations.txt", "Hello=你好")

            files, ignored = PackageScanner().scan(package(translation=True), archive, root / "out")

            self.assertEqual(
                [item.target for item in files],
                [
                    "AutoTranslator/Config.ini",
                    "AutoTranslator/Translation/zh-CN/Text/Translations.txt",
                ],
            )
            self.assertEqual(ignored, [])

    def test_xunity_translation_rejects_non_zip_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "Translations.txt"
            asset.write_text("Hello=你好", encoding="utf-8")
            with self.assertRaisesRegex(ScanError, "must use a ZIP"):
                PackageScanner().scan(package(translation=True), asset, root / "out")

    def test_rejects_zip_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escape.dll", b"not a dll")
            with self.assertRaises(ScanError):
                PackageScanner().scan(package(), archive, root / "out")
            self.assertFalse((root / "escape.dll").exists())

    def test_preserves_declared_managed_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "rooted.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("bundle/Mods/TestMod.dll", b"content")
                output.writestr("bundle/README.md", b"read me")
            files, ignored = PackageScanner().scan(package(), archive, root / "out")
            self.assertEqual([item.target for item in files], ["Mods/TestMod.dll"])
            self.assertEqual(ignored, ["bundle/README.md"])

    def test_non_dll_requires_override_even_under_allowed_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "data.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("UserData/TestMod/config.json", b"{}")
            files, ignored = PackageScanner().scan(package(), archive, root / "out")
            self.assertEqual(files, [])
            self.assertEqual(ignored, ["UserData/TestMod/config.json"])


if __name__ == "__main__":
    unittest.main()
