"""安装记录必须以磁盘为准：磁盘上没有的文件不显示为已安装。

覆盖两件事：
1. `reconcile`：手工删掉/改名/丢失的 DLL 对应的记录会被清掉，列表退回"纯扫描"结果；
2. 完整性判定是**实时**的：文件被改过 → `modified`（与安装记录不一致）；改后的内容不匹配
   任何发布版本 → `corrupted`（供 GUI 显示 + 重装/更新）。两者都不写进安装记录。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.application.service import ModManagerService  # noqa: E402
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402
from sprocket_mod_manager.infrastructure.config import ConfigStore  # noqa: E402
from sprocket_mod_manager.infrastructure.state import StateStore  # noqa: E402
from sprocket_mod_manager.presentation.web_gui import ClientApi  # noqa: E402

from test_adoption import FIXTURE_MOD, package  # noqa: E402


def install_melonloader(game: Path) -> None:
    """游戏根目录的 MelonLoader 布局：扫描 `Mods` 之前得先检测到运行时。"""
    (game / "version.dll").touch()
    (game / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    (game / "MelonLoader" / "net6" / "MelonLoader.dll").touch()


class DiskTruthTests(unittest.TestCase):
    PACKAGE_ID = "fixture.sprocket-mod"

    def _api(self, root: Path) -> tuple[ClientApi, Path, ModManagerService]:
        app_dir = root / "app"
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        install_melonloader(game)
        shutil.copyfile(FIXTURE_MOD, game / "Mods" / "FixtureMod.dll")
        ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
        service = ModManagerService(app_dir)
        service.registry = Registry(
            [package(self.PACKAGE_ID, "FixtureMod.dll", FIXTURE_MOD.read_bytes(), repository="fixture/FixtureMod")]
        )
        api = ClientApi("0.4.2", app_dir=app_dir, service_factory=lambda _app_dir: service)
        return api, game, service

    def test_hand_deleted_dll_leaves_no_ghost_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game, _service = self._api(Path(directory))
            try:
                self.assertTrue(api.adopt_existing()["changed"], "先把磁盘上的模组登记进记录")
                self.assertEqual([item["id"] for item in api.get_installed()["installed"]], [self.PACKAGE_ID])

                # 用户绕过管理器把 DLL 删了。
                (game / "Mods" / "FixtureMod.dll").unlink()

                after = api.get_installed()
            finally:
                api.install_queue.close()
                api.data.close()

            self.assertEqual(after["installed"], [], "文件没了就不许再显示为已安装")
            self.assertEqual(after["local_mods"], [], "磁盘上也没有这个模组了")

    def test_hand_renamed_dll_keeps_ownership_and_marks_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game, _service = self._api(Path(directory))
            try:
                self.assertTrue(api.adopt_existing()["changed"])
                # 绕过管理器手工改名（等价于"未经记录地禁用"）：`.dll` 与 `.dll.disable` 是同一个逻辑文件，
                # 所以归属必须保住，只是 disabled 变 true（记录里仍是规范路径）。
                (game / "Mods" / "FixtureMod.dll").rename(game / "Mods" / "FixtureMod.dll.disable")
                after = api.get_installed()
            finally:
                api.install_queue.close()
                api.data.close()

            self.assertEqual([item["id"] for item in after["installed"]], [self.PACKAGE_ID],
                             "手工改名不该丢掉归属（同一逻辑文件）")
            self.assertEqual(len(after["local_mods"]), 1, "模组仍然以本地扫描结果存在")
            row = after["local_mods"][0]
            self.assertEqual(row["path"], "Mods/FixtureMod.dll.disable")
            self.assertTrue(row["disabled"])
            self.assertEqual(row["installed_package_id"], self.PACKAGE_ID, "归属仍在")
            self.assertEqual(row["declared_id"], self.PACKAGE_ID, "DLL 元数据仍然认得它")

    def test_hand_renamed_dll_to_another_name_drops_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game, _service = self._api(Path(directory))
            try:
                self.assertTrue(api.adopt_existing()["changed"])
                # 改成**别的名字**才是真的"文件不在记录里了"：reconcile 必须清掉记录。
                (game / "Mods" / "FixtureMod.dll").rename(game / "Mods" / "RenamedMod.dll")
                after = api.get_installed()
            finally:
                api.install_queue.close()
                api.data.close()

            self.assertEqual(after["installed"], [], "换了文件名的记录要被清掉")
            row = after["local_mods"][0]
            self.assertEqual(row["path"], "Mods/RenamedMod.dll")
            self.assertEqual(row["installed_package_id"], "", "归属已丢，退回纯扫描")

    def test_verify_flags_modified_file_as_corrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game, _service = self._api(Path(directory))
            try:
                self.assertTrue(api.adopt_existing()["changed"])
                self.assertFalse(api.get_installed()["installed"][0]["corrupted"])
                self.assertEqual(api.get_installed()["installed"][0]["integrity"], "release")

                (game / "Mods" / "FixtureMod.dll").write_bytes(b"tampered payload")

                verified = api.verify_installed()
                state_text = (game / "SprocketModManager" / "installed.json").read_text(encoding="utf-8")
            finally:
                api.install_queue.close()
                api.data.close()

            self.assertTrue(verified["ok"], verified)
            self.assertEqual(verified["corrupted"], ["Mods/FixtureMod.dll"], "不匹配任何发布版本 → 损坏")
            self.assertEqual(verified["modified"], ["Mods/FixtureMod.dll"], "同时对不上安装记录")
            self.assertEqual(verified["checked"], 1)
            # 校验结果不在返回值里另带一份：它落进数据层，界面按推送重画。
            self.assertTrue(
                api.get_installed()["installed"][0]["corrupted"], "列表要能把损坏状态带给界面"
            )
            self.assertNotIn("corrupted", state_text, "判定结果不许写回安装记录（永远是实时算的）")

    def test_verify_clears_the_flag_once_the_file_matches_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game, _service = self._api(Path(directory))
            try:
                api.adopt_existing()
                shutil.copyfile(FIXTURE_MOD, game / "Mods" / "FixtureMod.dll")
                # 记录里现在记的是原始摘要，内容一致 → 不应标记损坏。
                verified = api.verify_installed()
            finally:
                api.install_queue.close()
                api.data.close()

            self.assertEqual(verified["corrupted"], [])
            self.assertEqual(verified["modified"], [])
            self.assertFalse(api.get_installed()["installed"][0]["corrupted"])

    def test_suppressed_file_is_no_longer_corrupted_but_visible_as_suppressed(self) -> None:
        """可以显式抑制损坏提示，但界面要能看出是"被抑制"而不是"正常"。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api, game, _service = self._api(root)
            try:
                api.adopt_existing()
                (game / "Mods" / "FixtureMod.dll").write_bytes(b"locally rebuilt payload")
                self.assertTrue(api.get_installed()["installed"][0]["corrupted"], "先确认它确实被判为损坏")

                # 用端点抑制：已归属的文件键应是「身份」形式，不是路径
                code = api.set_integrity_suppressed("Mods/FixtureMod.dll", True)
                self.assertTrue(code["ok"], code)
                self.assertEqual(code["suppressed"], ["fixture.sprocket-mod:FixtureMod.dll"],
                                 "抑制键跟着身份走（package id + 文件名）")

                after = api.get_installed()
            finally:
                api.install_queue.close()
                api.data.close()

            row = after["installed"][0]
            self.assertFalse(row["corrupted"], "被抑制的文件不该再报红")
            self.assertTrue(row["suppressed"], "但要能看出它是被抑制的，而不是正常")
            self.assertEqual(row["integrity"], "suppressed")
            path = game / "SprocketModManager" / "installed.json"
            self.assertNotIn("suppressed", path.read_text(encoding="utf-8"),
                             "抑制状态存在**游戏目录**的 suppression.json 里，不进安装记录")
            listing = game / "SprocketModManager" / "suppression.json"
            self.assertTrue(listing.is_file(), "名单跟状态一起留在游戏目录（AppData 只放管理器自己的配置）")

    def test_a_fileless_modloader_is_neither_corrupted_nor_missing(self) -> None:
        """基础运行时不记逐文件哈希：完整性判定与 `verify` 都不许把它算成损坏或缺文件。"""
        with tempfile.TemporaryDirectory() as directory:
            api, game, _service = self._api(Path(directory))
            try:
                store = StateStore(game / "SprocketModManager" / "installed.json")
                state = store.load()
                state["packages"]["lavagang.melonloader"] = {
                    "id": "lavagang.melonloader",
                    "name": "MelonLoader",
                    "version": "0.7.3",
                    "requested": True,
                    "kind": "modloader",
                    "files": [],
                    "directories": ["MelonLoader"],
                }
                store.save(state)

                installed = api.get_installed()
                verified = api.verify_installed()
            finally:
                api.install_queue.close()
                api.data.close()

        loader = next(item for item in installed["installed"] if item["id"] == "lavagang.melonloader")
        self.assertFalse(loader["corrupted"], "加载器没有逐文件哈希，不许报损坏")
        self.assertEqual(loader["integrity"], "local")
        self.assertEqual(verified["corrupted"], [])
        self.assertEqual(verified["missing"], [])

    def test_suppression_survives_disable_and_enable(self) -> None:
        """抑制键跟着身份走，禁用/启用（`.dll` ↔ `.dll.disable`）不会丢。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api, game, _service = self._api(root)
            try:
                api.adopt_existing()
                (game / "Mods" / "FixtureMod.dll").write_bytes(b"locally rebuilt payload")

                # 用**禁用后的文件名**去抑制：键仍然是身份形式，且文件名去掉 `.disable`
                code = api.set_integrity_suppressed("Mods/FixtureMod.dll.disable", True)
                self.assertTrue(code["ok"], code)
                self.assertEqual(code["suppressed"], ["fixture.sprocket-mod:FixtureMod.dll"])

                disabled = api.toggle_mod("Mods/FixtureMod.dll", False)
                self.assertTrue(disabled["ok"], disabled)
                self.assertTrue((game / "Mods" / "FixtureMod.dll.disable").is_file())

                after_disable = api.get_installed()["installed"][0]
                self.assertTrue(after_disable["suppressed"], "禁用之后抑制状态必须还在")
                self.assertFalse(after_disable["corrupted"])

                enabled = api.toggle_mod("Mods/FixtureMod.dll.disable", True)
                self.assertTrue(enabled["ok"], enabled)
                after_enable = api.get_installed()["installed"][0]
                self.assertTrue(after_enable["suppressed"], "启用回来抑制状态仍然在")
                self.assertFalse(after_enable["corrupted"])

                removed = api.set_integrity_suppressed("Mods/FixtureMod.dll", False)
                self.assertEqual(removed["suppressed"], [])
                self.assertTrue(api.get_installed()["installed"][0]["corrupted"], "取消抑制后重新报红")
            finally:
                api.install_queue.close()
                api.data.close()

    def test_stale_suppression_entries_are_dropped_automatically(self) -> None:
        """失效条目（包记录没了、路径也没了）在下次读取时自动从名单里清掉。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api, game, _service = self._api(root)
            try:
                api.adopt_existing()
                (game / "Mods" / "FixtureMod.dll").write_bytes(b"locally rebuilt payload")
                self.assertTrue(api.set_integrity_suppressed("Mods/FixtureMod.dll", True)["ok"])

                from sprocket_mod_manager.infrastructure.suppression_store import store_for

                listing = store_for(game)
                listing.save([*listing.load(), "gone.package:Gone.dll", "Mods/AlsoGone.dll"])

                # 下一次读取（GUI 的 get_installed 路径）就该把死条目清掉
                after = api.get_installed()
                pruned = listing.load()
            finally:
                api.install_queue.close()
                api.data.close()

            self.assertEqual(pruned, ["fixture.sprocket-mod:FixtureMod.dll"],
                             "只剩还有效的那条；包记录没了/路径没了的都被清掉")
            self.assertTrue(after["installed"][0]["suppressed"], "有效条目继续生效")

    def test_disabling_a_mod_does_not_make_it_corrupted(self) -> None:
        """禁用只是改名：判定要照到 `.dll.disable`，不许把它当"读不到"→整包报损坏。

        拿状态里的规范路径直接去算 hash，`.dll.disable` 那份会读不到 → `unreadable` → 包被判损坏，
        所以判定按"规范名 → `.dll.disable`"的顺序找到真实文件再算。
        """
        with tempfile.TemporaryDirectory() as directory:
            api, game, _service = self._api(Path(directory))
            try:
                api.adopt_existing()
                before = api.get_installed()["installed"][0]
                self.assertEqual(before["integrity"], "release", "先确认启用时正常")

                disabled = api.toggle_mod("Mods/FixtureMod.dll", False)
                self.assertTrue(disabled["ok"], disabled)
                self.assertTrue((game / "Mods" / "FixtureMod.dll.disable").is_file())

                after = api.get_installed()["installed"][0]
                self.assertFalse(after["corrupted"], "禁用不许变成损坏")
                self.assertEqual(after["integrity"], "release", "内容没变，判定结果也不该变")

                # verify 走的是另一条路（强制重算），同样不许因为改名而报损坏
                verified = api.verify_installed()
                self.assertEqual(verified["corrupted"], [])
                self.assertEqual(verified["missing"], [])
            finally:
                api.install_queue.close()
                api.data.close()

if __name__ == "__main__":
    unittest.main()
