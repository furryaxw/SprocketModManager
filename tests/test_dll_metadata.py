"""`sprocket_mod_manager.infrastructure.dll_metadata` 的离线测试。

夹具是 `tests/fixtures/dll_metadata/dll/` 里已提交的极小托管 DLL（< 16 KB），
测试只读这些字节，不调用 dotnet、不加载任何程序集。
"""

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from sprocket_mod_manager.domain.errors import ScanError
from sprocket_mod_manager.infrastructure.dll_metadata import (
    MELON_KIND_MODS,
    MELON_KIND_PLUGINS,
    melon_info_description,
    read_dll_metadata,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dll_metadata" / "dll"
FIXTURE_MOD = FIXTURE_DIR / "FixtureMod.dll"
FIXTURE_PLUGIN = FIXTURE_DIR / "FixturePlugin.dll"
FIXTURE_LIBRARY = FIXTURE_DIR / "FixtureLibrary.dll"
REQUIRED_FIXTURES = (FIXTURE_MOD, FIXTURE_PLUGIN, FIXTURE_LIBRARY)
NATIVE_DLL = Path("C:/Windows/System32/version.dll")
DEPLOYED_MOD = Path("G:/Sprocket/Mods/SprocketJitterFix.dll")

# 夹具源码 / 生成脚本见 tests/fixtures/dll_metadata/；DLL 已提交，CI 不需要 dotnet。
SPROCKET_KEYS = {
    "id",
    "display_name",
    "description",
    "authors",
    "homepage",
    "repository",
    "category",
    "license",
}


def setUpModule() -> None:
    missing = [str(path) for path in REQUIRED_FIXTURES if not path.is_file()]
    if missing:
        raise AssertionError(
            "missing committed fixture DLLs (run tests/fixtures/dll_metadata/build_fixtures.ps1): "
            + ", ".join(missing)
        )


class FixtureModTests(unittest.TestCase):
    """继承 MelonMod + MelonInfo + 全套 Sprocket.Mod.* 的正常夹具。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.metadata = read_dll_metadata(FIXTURE_MOD)

    def test_is_managed_and_not_native(self) -> None:
        self.assertTrue(self.metadata.is_managed)
        self.assertFalse(self.metadata.is_native)
        self.assertEqual(self.metadata.errors, ())

    def test_reads_assembly_identity(self) -> None:
        self.assertEqual(self.metadata.assembly_name, "FixtureMod")
        self.assertEqual(self.metadata.assembly_version, "1.2.0.0")
        self.assertEqual(self.metadata.file_version, "1.2.3.4")
        self.assertTrue(self.metadata.target_framework.startswith(".NETCoreApp"), self.metadata.target_framework)

    def test_reads_melon_info(self) -> None:
        self.assertEqual(self.metadata.melon_kind, MELON_KIND_MODS)
        self.assertEqual(self.metadata.melon_name, "Fixture Mod")
        self.assertEqual(self.metadata.melon_version, "1.2.3")
        self.assertEqual(self.metadata.melon_author, "Fixture Author")
        self.assertEqual(self.metadata.melon_download_link, "https://example.invalid/fixture-mod")
        self.assertEqual(self.metadata.melon_credits, "Fixture Helper")

    def test_reads_all_sprocket_keys(self) -> None:
        self.assertEqual(
            self.metadata.sprocket,
            {
                "id": "fixture.sprocket-mod",
                "display_name": "Fixture Mod",
                "description": "测试用夹具模组。",
                "authors": "Fixture Author,Second Author",
                "homepage": "https://example.invalid/",
                "repository": "fixture/FixtureMod",
                "category": "utility",
                "license": "AGPL-3.0-only",
            },
        )
        self.assertLessEqual(SPROCKET_KEYS, set(self.metadata.sprocket))

    def test_unknown_sprocket_keys_are_dropped(self) -> None:
        # 未知的 Sprocket.Mod.* 键与非 Sprocket 键都不进 sprocket 字段（`repositoryurl` 也一样），
        # 只留规范化后的键。
        self.assertNotIn("experimental_flag", self.metadata.sprocket)
        self.assertNotIn("repositoryurl", self.metadata.sprocket)

    def test_reads_dependency_and_incompatibility_attributes(self) -> None:
        # 夹具声明了 ("SprocketDepth", " SprocketModAPI ", "SprocketDepth")：必须裁剪空白并去重。
        self.assertEqual(self.metadata.required_dependencies, ("SprocketDepth", "SprocketModAPI"))
        self.assertEqual(self.metadata.incompatible_assemblies, ("LegacyOverhaul",))

    def test_description_helper_summarises_melon_info(self) -> None:
        self.assertEqual(
            melon_info_description(self.metadata),
            "Fixture Mod 1.2.3 by Fixture Author (https://example.invalid/fixture-mod)",
        )

    def test_metadata_is_frozen(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            self.metadata.melon_name = "changed"  # type: ignore[misc]


class FixturePluginTests(unittest.TestCase):
    """继承 MelonPlugin，并走「数字版本」构造重载。"""

    def test_reads_plugin_kind_and_numeric_version(self) -> None:
        metadata = read_dll_metadata(FIXTURE_PLUGIN)
        self.assertTrue(metadata.is_managed)
        self.assertEqual(metadata.errors, ())
        self.assertEqual(metadata.melon_kind, MELON_KIND_PLUGINS)
        self.assertEqual(metadata.melon_name, "Fixture Plugin")
        self.assertEqual(metadata.melon_version, "2.5.1")
        self.assertEqual(metadata.melon_author, "Fixture Author")
        self.assertIsNone(metadata.melon_download_link)
        self.assertIsNone(metadata.melon_credits)
        self.assertEqual(metadata.assembly_version, "2.5.1.0")
        self.assertEqual(metadata.file_version, "2.5.1.9")


class FixtureLibraryTests(unittest.TestCase):
    """只含 AssemblyMetadata 的纯托管库：没有 MelonInfo，其余字段照常。"""

    def test_has_no_melon_kind_but_keeps_other_fields(self) -> None:
        metadata = read_dll_metadata(FIXTURE_LIBRARY)
        self.assertTrue(metadata.is_managed)
        self.assertFalse(metadata.is_native)
        self.assertEqual(metadata.errors, ())
        self.assertIsNone(metadata.melon_kind)
        self.assertIsNone(metadata.melon_name)
        self.assertIsNone(metadata.melon_version)
        self.assertIsNone(metadata.melon_author)
        self.assertIsNone(metadata.melon_download_link)
        self.assertIsNone(metadata.melon_credits)
        self.assertIsNone(melon_info_description(metadata))
        self.assertEqual(metadata.assembly_name, "FixtureLibrary")
        self.assertEqual(metadata.assembly_version, "3.0.0.0")
        self.assertEqual(metadata.file_version, "3.0.0.1")
        self.assertTrue(metadata.target_framework.startswith(".NETCoreApp"))
        self.assertEqual(metadata.sprocket, {
            "id": "fixture.sprocket-library",
            "display_name": "Fixture Library",
        })


class DegradationTests(unittest.TestCase):
    """畸形 / 非托管输入必须降级，不得抛异常；只有根本性错误才抛。"""

    def test_fixture_dlls_stay_small(self) -> None:
        for path in REQUIRED_FIXTURES:
            self.assertLess(path.stat().st_size, 16 * 1024, path.name)

    def test_malformed_pe_degrades_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "Broken.dll"
            broken.write_bytes(b"MZ" + b"\x00" * 32)
            metadata = read_dll_metadata(broken)
        self.assertFalse(metadata.is_managed)
        self.assertFalse(metadata.is_native)
        self.assertIsNone(metadata.assembly_name)
        self.assertIsNone(metadata.melon_kind)
        self.assertEqual(metadata.sprocket, {})
        self.assertTrue(metadata.errors)

    def test_non_pe_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            text = Path(directory) / "notes.txt"
            text.write_text("this is not a PE file", encoding="utf-8")
            with self.assertRaises(ScanError):
                read_dll_metadata(text)

    def test_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ScanError):
                read_dll_metadata(Path(directory) / "Missing.dll")

    def test_pathlike_and_str_are_equivalent(self) -> None:
        self.assertEqual(read_dll_metadata(str(FIXTURE_LIBRARY)), read_dll_metadata(FIXTURE_LIBRARY))

    @unittest.skipUnless(NATIVE_DLL.is_file(), "no native System32 DLL available")
    def test_native_dll_is_flagged_and_reads_fixed_file_info(self) -> None:
        metadata = read_dll_metadata(NATIVE_DLL)
        self.assertFalse(metadata.is_managed)
        self.assertTrue(metadata.is_native)
        self.assertIsNone(metadata.assembly_name)
        self.assertIsNone(metadata.melon_kind)
        # 原生 DLL 没有 AssemblyFileVersionAttribute，回退到 PE 的 VS_FIXEDFILEINFO。
        self.assertIsNotNone(metadata.file_version)
        self.assertEqual(metadata.errors, ())


@unittest.skipUnless(DEPLOYED_MOD.is_file(), "deployed Sprocket mods are not available in CI")
class DeployedModSmokeTests(unittest.TestCase):
    """对真实已部署模组的冒烟测试（本机有才跑，CI 上没有这个目录）。"""

    def test_reads_deployed_mod(self) -> None:
        metadata = read_dll_metadata(DEPLOYED_MOD)
        self.assertTrue(metadata.is_managed)
        self.assertEqual(metadata.assembly_name, "SprocketJitterFix")
        self.assertEqual(metadata.melon_kind, MELON_KIND_MODS)
        self.assertEqual(metadata.melon_name, "LayingDrive Jitter Fix")
        self.assertEqual(metadata.melon_version, "0.9.0")
        self.assertEqual(metadata.melon_author, "furryAxw")
        self.assertEqual(metadata.target_framework, ".NETCoreApp,Version=v6.0")
        self.assertEqual(metadata.errors, ())


if __name__ == "__main__":
    unittest.main()
