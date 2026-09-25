from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import modman
from sprocket_mod_manager.application.local_mods import scan_local_mods
from sprocket_mod_manager.domain.models import RegistryPackage
from sprocket_mod_manager.infrastructure.state import StateStore

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dll_metadata" / "dll"
FIXTURE_MOD = FIXTURE_DIR / "FixtureMod.dll"


def install_melonloader(game: Path) -> None:
    """游戏根目录的 MelonLoader 布局：扫描 `Mods` 之前得先检测到运行时。"""
    (game / "version.dll").touch()
    (game / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    (game / "MelonLoader" / "net6" / "MelonLoader.dll").touch()


def make_package(package_id: str, name: str, authors: tuple[str, ...] = ("Fixture Author",)) -> RegistryPackage:
    return RegistryPackage(
        id=package_id,
        name=name,
        authors=authors,
        repository=f"furryaxw/{name}",
        license="GPL-3.0-only",
        display_name={"en": "Fixture Mod"},
        description={"en": "Fixture description"},
        release={},
        dependencies=(),
        install={},
        category="utility",
        tags=("fixture",),
    )


class CliLocalModsTests(unittest.TestCase):
    """`modman.py local-mods` / `disable` / `enable` 的端到端行为（临时游戏目录，不触碰真实安装）。"""

    def _prepare(self, root: Path) -> tuple[Path, Path, Path]:
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        install_melonloader(game)
        shutil.copyfile(FIXTURE_MOD, game / "Mods" / "FixtureMod.dll")
        index = root / "index.json"
        index.write_text(json.dumps({"schema_version": 1, "packages": []}), encoding="utf-8")
        return game, index, root / "app"

    def _run(self, app: Path, index: Path, game: Path, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = modman.cli_main(
                ["--app-dir", str(app), "--index-file", str(index), "--game-path", str(game), *argv]
            )
        return code, buffer.getvalue()

    def test_local_mods_lists_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game, index, app = self._prepare(Path(directory))
            code, output = self._run(app, index, game, "local-mods")
            self.assertEqual(code, 0, output)
            self.assertIn("Fixture Mod", output)
            self.assertIn("1.2.3", output)
            self.assertIn("Mods/FixtureMod.dll", output)
            self.assertIn("total=1", output)
            self.assertIn("missing_dependencies=1", output,
                          "夹具声明了 SprocketDepth，但临时游戏目录里没有它")
            self.assertIn("missing=SprocketDepth", output,
                          "缺失依赖由 DLL 元数据算出并打印在行内")

            code, output = self._run(app, index, game, "--json", "local-mods")
            self.assertEqual(code, 0, output)
            payload = json.loads(output)
            self.assertEqual(payload["summary"]["total"], 1)
            self.assertEqual(payload["mods"][0]["display_name"], "Fixture Mod")

    def test_disable_and_enable_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game, index, app = self._prepare(Path(directory))

            code, output = self._run(app, index, game, "disable", "Mods/FixtureMod.dll")
            self.assertEqual(code, 0, output)
            self.assertIn("restart Sprocket to apply", output)
            self.assertTrue((game / "Mods" / "FixtureMod.dll.disable").is_file())
            self.assertFalse((game / "Mods" / "FixtureMod.dll").exists())

            code, output = self._run(app, index, game, "local-mods")
            self.assertEqual(code, 0, output)
            self.assertIn("disabled=1", output)
            self.assertIn("disabled", output)

            code, output = self._run(app, index, game, "enable", "Mods/FixtureMod.dll.disable")
            self.assertEqual(code, 0, output)
            self.assertTrue((game / "Mods" / "FixtureMod.dll").is_file())

    def test_local_mods_writes_the_metadata_cache(self) -> None:
        """命令行也要落盘元数据缓存，否则每次 `modman local-mods` 都是冷的（实测 ~2.8 s）。"""
        with tempfile.TemporaryDirectory() as directory:
            game, index, app = self._prepare(Path(directory))
            code, output = self._run(app, index, game, "--json", "local-mods")
            self.assertEqual(code, 0, output)
            self.assertTrue((game / "SprocketModManager" / "file-metadata.json").is_file(),
                            "扫描必须自己把缓存写进 <game>/SprocketModManager/file-metadata.json"
                            "（DLL 元数据独立成文件，且游戏目录相关的东西不许放 AppData）")

    def test_verify_reports_modified_managed_file(self) -> None:
        """`modman verify` 用真实 SHA-256 判定"文件在但和安装记录对不上"。

        这条索引是**空 Registry**（没有任何发布数据）⇒ 按口径只报 `modified`，不报 `corrupted`：
        "损坏"的定义是"不符合任何一个发布版本"，没有发布数据就无从判断。
        """
        with tempfile.TemporaryDirectory() as directory:
            game, index, app = self._prepare(Path(directory))
            store = StateStore(game / "SprocketModManager" / "installed.json")
            store.save({
                "schema_version": 1,
                "files": {"Mods/FixtureMod.dll": {
                    "sha256": "0" * 64, "owners": ["fixture.sprocket-mod"], "requested": True,
                }},
                "packages": {"fixture.sprocket-mod": {
                    "name": "FixtureMod", "version": "1.2.3", "requested": True,
                    "files": ["Mods/FixtureMod.dll"], "dependencies": [],
                }},
            })

            code, output = self._run(app, index, game, "--json", "verify")
            self.assertEqual(code, 0, output)
            payload = json.loads(output)
            self.assertEqual(payload["checked"], 1)
            self.assertEqual(payload["modified"], ["Mods/FixtureMod.dll"])
            self.assertEqual(payload["corrupted"], [], "没有发布数据时不许报损坏")
            self.assertFalse((game / "SprocketModManager" / "installed.json").read_text(encoding="utf-8").find("corrupted") >= 0,
                             "校验结果不许写回安装记录（判断永远是实时的）")

    def test_suppress_and_unsuppress_write_the_game_side_list(self) -> None:
        """抑制名单存在**游戏目录**的 `SprocketModManager/suppression.json`（AppData 里不留）。"""
        with tempfile.TemporaryDirectory() as directory:
            game, index, app = self._prepare(Path(directory))
            listing = game / "SprocketModManager" / "suppression.json"

            code, output = self._run(app, index, game, "suppress", "Mods/SprocketModAPI.dll")
            self.assertEqual(code, 0, output)
            self.assertEqual(json.loads(listing.read_text(encoding="utf-8"))["suppressed"],
                             ["Mods/SprocketModAPI.dll"])

            # `.dll.disable` 是同一个逻辑文件：抑制键归一成规范路径
            code, output = self._run(app, index, game, "suppress", "Mods/FixtureMod.dll.disable")
            self.assertEqual(code, 0, output)
            self.assertEqual(json.loads(listing.read_text(encoding="utf-8"))["suppressed"],
                             ["Mods/FixtureMod.dll", "Mods/SprocketModAPI.dll"])

            code, output = self._run(app, index, game, "unsuppress", "Mods/SprocketModAPI.dll")
            self.assertEqual(code, 0, output)
            self.assertEqual(json.loads(listing.read_text(encoding="utf-8"))["suppressed"],
                             ["Mods/FixtureMod.dll"])

    def test_deleted_managed_file_is_pruned_before_listing(self) -> None:
        """本地文件删了就不许再出现在 `installed` 里（纯扫描模型的底线）。"""
        with tempfile.TemporaryDirectory() as directory:
            game, index, app = self._prepare(Path(directory))
            store = StateStore(game / "SprocketModManager" / "installed.json")
            store.save({
                "schema_version": 1,
                "files": {"Mods/GhostMod.dll": {
                    "sha256": "1" * 64, "owners": ["ghost.mod"], "requested": True,
                }},
                "packages": {"ghost.mod": {
                    "name": "GhostMod", "version": "1.0.0", "requested": True,
                    "files": ["Mods/GhostMod.dll"], "dependencies": [],
                }},
            })

            code, output = self._run(app, index, game, "--json", "installed")
            self.assertEqual(code, 0, output)
            self.assertEqual(json.loads(output), {})

    def test_disable_and_enable_sync_the_install_record(self) -> None:
        """CLI 改名必须同步安装记录（与 GUI 的 toggle_mod 同一条路径）。

        不同步的话，禁用一个受管 DLL 之后状态里还留着旧路径，
        下次刷新就会显示"包已安装但文件不存在"的幽灵条目。
        """
        with tempfile.TemporaryDirectory() as directory:
            game, index, app = self._prepare(Path(directory))
            target = game / "Mods" / "FixtureMod.dll"
            state_path = game / "SprocketModManager" / "installed.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({
                "schema_version": 1,
                "packages": {"fixture.sprocket-mod": {
                    "name": "FixtureMod", "version": "1.2.3", "requested": True, "dependencies": [],
                    "files": ["Mods/FixtureMod.dll"], "installed_at": "2026-09-22T00:00:00Z",
                    "install_mode": "standard",
                }},
                "files": {"Mods/FixtureMod.dll": {
                    "owners": ["fixture.sprocket-mod"],
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "preexisting": False, "disabled": False,
                }},
            }), encoding="utf-8")

            code, output = self._run(app, index, game, "disable", "Mods/FixtureMod.dll")
            self.assertEqual(code, 0, output)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            disabled_paths = [entry["path"] for package in state["packages"].values()
                              for entry in package["files"]]
            self.assertEqual(disabled_paths, ["Mods/FixtureMod.dll"],
                             "记录用规范路径（.dll），禁用只体现在 disabled 标志上")
            self.assertTrue(state["packages"]["fixture.sprocket-mod"]["files"][0]["disabled"])
            self.assertTrue((game / "Mods" / "FixtureMod.dll.disable").is_file())

            code, output = self._run(app, index, game, "enable", "Mods/FixtureMod.dll.disable")
            self.assertEqual(code, 0, output)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            enabled_paths = [entry["path"] for package in state["packages"].values()
                             for entry in package["files"]]
            self.assertEqual(enabled_paths, ["Mods/FixtureMod.dll"], "启用后归属回到 .dll")
            self.assertFalse(state["packages"]["fixture.sprocket-mod"]["files"][0]["disabled"])

    def test_unsafe_path_fails_with_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game, index, app = self._prepare(Path(directory))
            for candidate in ("../outside.dll", "UserData/thing.dll"):
                with contextlib.redirect_stderr(io.StringIO()):
                    buffer = io.StringIO()
                    with contextlib.redirect_stdout(buffer):
                        code = modman.cli_main(
                            ["--app-dir", str(app), "--index-file", str(index), "--game-path", str(game), "disable", candidate]
                        )
                self.assertEqual(code, 1, candidate)
                self.assertFalse((game.parent / "outside.dll").exists())


class ScanWithRegistryTests(unittest.TestCase):
    """CLI 用的扫描函数在带 Registry 时给出匹配结果。"""

    def test_declared_id_matches_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            (game / "Mods").mkdir(parents=True)
            (game / "Sprocket.exe").touch()
            install_melonloader(game)
            shutil.copyfile(FIXTURE_MOD, game / "Mods" / "FixtureMod.dll")
            packages = [make_package("fixture.sprocket-mod", "FixtureMod")]
            mods = scan_local_mods(game, {}, packages)
            self.assertEqual(len(mods), 1)
            self.assertEqual(mods[0].registry_id, "fixture.sprocket-mod")
            self.assertEqual(mods[0].registry_match, "declared-id")


if __name__ == "__main__":
    unittest.main()
