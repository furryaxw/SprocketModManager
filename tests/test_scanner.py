import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath

from sprocket_mod_manager.domain.errors import ScanError
from sprocket_mod_manager.domain.models import RegistryPackage
from sprocket_mod_manager.infrastructure.scanner import PackageScanner

TRANSLATION_RULE = {"match": "**", "type": "xunity:translation", "layout": "tree"}


def package(*, translation=False):
    file_rules = (TRANSLATION_RULE,) if translation else ()
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
            **(
                {"mode": "patch", "files": [dict(TRANSLATION_RULE)]}
                if translation
                else {}
            ),
        },
        category="translation" if translation else "utility",
        tags=(),
        file_rules=file_rules,
        schema_version=2 if translation else 1,
    )


def translation_scanner() -> PackageScanner:
    return PackageScanner({"xunity:translation": PurePosixPath("AutoTranslator")})


class ScannerTests(unittest.TestCase):
    def test_a_type_rule_installs_the_tree_into_its_supply_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "zh_cn.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("Config.ini", b"language=zh-CN")
                output.writestr("Translation/zh-CN/Text/Translations.txt", "Hello=你好")

            files, ignored = translation_scanner().scan(
                package(translation=True), archive, root / "out"
            )

            self.assertEqual(
                [item.target for item in files],
                [
                    "AutoTranslator/Config.ini",
                    "AutoTranslator/Translation/zh-CN/Text/Translations.txt",
                ],
            )
            self.assertEqual(ignored, [])

    def test_an_unsupported_asset_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "Translations.txt"
            asset.write_text("Hello=你好", encoding="utf-8")
            with self.assertRaisesRegex(ScanError, "unsupported Release asset type"):
                translation_scanner().scan(package(translation=True), asset, root / "out")

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
