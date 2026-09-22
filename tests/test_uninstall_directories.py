"""卸载按安装时记录的文件夹列表执行：新建的目录记进包记录，卸载时删空目录。

（`apply` 把新建的目录写进 `package["directories"]`；`remove` 自深到浅删空目录。）
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from sprocket_mod_manager.domain.models import (  # noqa: E402
    PreparedFile,
    PreparedPackage,
    PreparedPlan,
    RegistryPackage,
    ReleaseInfo,
    ResolvedPackage,
    ResolutionPlan,
)
from sprocket_mod_manager.domain.semver import Version  # noqa: E402
from sprocket_mod_manager.infrastructure.installer import Installer  # noqa: E402
from sprocket_mod_manager.infrastructure.state import StateStore  # noqa: E402
from sprocket_mod_manager.utilities.checksums import sha256_file  # noqa: E402


def _prepared(root: Path, targets: dict[str, bytes]) -> PreparedPlan:
    package = RegistryPackage(
        id="fixture.nested", name="Nested Mod", authors=("fixture",), repository="fixture/Nested",
        license="MIT", display_name={"en": "Nested"}, description={"en": "nested install"},
        release={}, dependencies=(), install={}, category="utility", tags=(),
    )
    release = ReleaseInfo(1, "v1.0.0", Version.parse("1.0.0"), False, "", ())
    resolved = ResolvedPackage(package, release, ())
    files = []
    for index, (target, payload) in enumerate(targets.items()):
        source = root / f"source-{index}.dll"
        source.write_bytes(payload)
        files.append(PreparedFile(package.id, source, source.name, target, sha256_file(source)))
    return PreparedPlan(ResolutionPlan(package.id, (resolved,)), [PreparedPackage(resolved, files=files)], root)


class UninstallDirectoriesTests(unittest.TestCase):
    def _installer(self, root: Path) -> tuple[Installer, Path]:
        game = root / "game"
        game.mkdir(parents=True, exist_ok=True)
        (game / "Sprocket.exe").write_bytes(b"stub")
        return Installer(root / "app", StateStore(root / "app" / "installed.json")), game

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_install_records_created_directories_and_remove_deletes_them(self, _running):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer, game = self._installer(root)
            nested = "Mods/Deep/Pack/Mod.dll"

            installer.apply(_prepared(root, {nested: b"payload"}), game)

            record = installer.state_store.load()["packages"]["fixture.nested"]
            self.assertEqual(record.get("directories"), ["Mods/Deep", "Mods/Deep/Pack"],
                             "新建的目录要按安装时记录（自浅到深）")
            self.assertTrue((game / nested).is_file())

            installer.remove("fixture.nested", game)

            self.assertFalse((game / nested).exists(), "文件按记录删除")
            self.assertFalse((game / "Mods" / "Deep").exists(), "空目录按记录删除")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_remove_keeps_directories_that_still_have_files(self, _running):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer, game = self._installer(root)
            nested = "Mods/Deep/Pack/Mod.dll"
            installer.apply(_prepared(root, {nested: b"payload"}), game)

            # 用户往这个目录里放了自己的东西：卸载不许连它一起删。
            (game / "Mods" / "Deep" / "Pack" / "keep.txt").write_bytes(b"mine")
            installer.remove("fixture.nested", game)

            self.assertFalse((game / nested).exists(), "受管文件仍被删除")
            self.assertTrue((game / "Mods" / "Deep" / "Pack" / "keep.txt").is_file(),
                            "非空目录必须保留")

    @patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False)
    def test_preexisting_directories_are_not_recorded(self, _running):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer, game = self._installer(root)
            (game / "Mods" / "Existing").mkdir(parents=True)
            installer.apply(_prepared(root, {"Mods/Existing/Mod.dll": b"payload"}), game)

            record = installer.state_store.load()["packages"]["fixture.nested"]
            self.assertEqual(record.get("directories"), [],
                             "本来就存在的目录不算我们创建的，不记也不删")


if __name__ == "__main__":
    unittest.main()
