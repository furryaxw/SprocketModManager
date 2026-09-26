"""四份读数说的是同一件事：装 / 卸 / 换目录之后都不许各说各话。

这是这次架构重构的验收门。长期读数都在数据层里，一次写操作之后它们必须自己对齐 ——
侧栏（`environment`）、加载器页（`loaders`）、已安装页（`installed`）读的是同一份事实，
谁都不该落后半步；换了游戏目录之后，上一个目录的读数一个都不许残留。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# 复用 test_environment 里的夹具构造器（游戏目录、加载器包、假窗口、真 DLL）。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_adoption import package as mod_package  # noqa: E402
from test_environment import (  # noqa: E402
    FIXTURE_MOD,
    LOADER_ID,
    _FakeWindow,
    _loader_table,
    detected_melonloader,
    game_dir_with_version,
    loader_package,
    unity_payload,
)
from sprocket_mod_manager.application.service import ModManagerService
from sprocket_mod_manager.domain.registry import Registry
from sprocket_mod_manager.infrastructure.config import ConfigStore
from sprocket_mod_manager.presentation.web_gui import ClientApi

# 界面订阅的那几个 key：验收就在这几份读数之间比。
#
# 不订 `catalog`：订它会让数据层按配置里的索引现去拉注册表（那是真实路径），而这里要的是一个
# 本地构造的、确定的注册表。目录页那份"这个包装没装"是 `data.js` 从 `installed` 现查出来的派生结果，
# 不存第二份 —— 它不可能和已安装读数不一致。
UI_KEYS = ("installed", "environment", "loaders")


class ReadingAgreementTests(unittest.TestCase):
    """装 / 卸 / 换目录之后，几份长期读数必须说同一件事。"""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        # 目录最后才删：`addCleanup` 是后进先出，先登记它，客户端那边登记的收尾就会先跑。
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def _client(self, game: Path) -> ClientApi:
        app_dir = self.root / "app"
        ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
        service = ModManagerService(app_dir)
        api = ClientApi("test", app_dir=app_dir, service_factory=lambda _app_dir: service)
        # 注册表里放一个加载器 + 一个模组：认领要能对上包，加载器那份读数才有东西可看。
        api.service.registry = Registry(
            [
                loader_package(),
                mod_package(
                    "fixture.sprocket-mod",
                    "FixtureMod.dll",
                    FIXTURE_MOD.read_bytes(),
                    repository="fixture/FixtureMod",
                ),
            ],
            _loader_table(),
        )
        api.bind_window(_FakeWindow())
        api.data_subscribe(list(UI_KEYS))
        self.addCleanup(api.data.close)
        self.addCleanup(api._environment_monitor.stop)
        self.addCleanup(api.install_queue.close)
        return api

    def _wait(self, api: ClientApi, key: str, predicate, timeout: float = 15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = api.data.get(key)
            if value is not None and predicate(value):
                return value
            time.sleep(0.05)
        self.fail(f"{key} 没有变成期望的样子：{api.data.get(key)!r}")

    @staticmethod
    def _installed_loaders(api: ClientApi) -> set[str]:
        """已安装那份读数里的基础运行时（种类由数据层给出：`kind`）。"""
        payload = api.data.get("installed") or {}
        return {
            str(item["id"])
            for item in payload.get("installed", [])
            if item.get("kind") == "modloader"
        }

    @staticmethod
    def _environment_loaders(api: ClientApi) -> set[str]:
        """侧栏那份读数里"装着"的加载器。"""
        payload = api.data.get("environment") or {}
        return {
            str(loader_id)
            for loader_id, info in (payload.get("loaders") or {}).items()
            if info.get("installed")
        }

    @staticmethod
    def _loader_page_loaders(api: ClientApi) -> set[str]:
        """加载器页那份读数里"已安装"的包。"""
        payload = api.data.get("loaders") or {}
        return {
            str(item["id"])
            for item in payload.get("modloaders", [])
            if item.get("installed")
        }

    def _every_reading(self, api: ClientApi) -> tuple[set[str], set[str], set[str]]:
        return (
            self._installed_loaders(api),
            self._environment_loaders(api),
            self._loader_page_loaders(api),
        )

    def _wait_agreement(self, api: ClientApi, expected: set[str], timeout: float = 15.0) -> None:
        """等三份读数**都**收敛到同一个答案；等不到就把当时的三份摊出来。"""
        deadline = time.time() + timeout
        last: tuple[set[str], set[str], set[str]] = (set(), set(), set())
        while time.time() < deadline:
            last = self._every_reading(api)
            if all(reading == expected for reading in last):
                return
            time.sleep(0.05)
        self.fail(f"三份读数没有对齐到 {expected or '空'}：已安装={last[0]} 侧栏={last[1]} 加载器页={last[2]}")

    def test_adopting_a_loader_is_reported_the_same_way_everywhere(self) -> None:
        game = game_dir_with_version(self.root, unity_payload("0.2.53.2"))
        api = self._client(game)
        detected_melonloader(game)

        with patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False):
            self.assertTrue(api.adopt_existing()["ok"])

        self._wait_agreement(api, {LOADER_ID})

    def test_uninstalling_a_loader_is_reported_the_same_way_everywhere(self) -> None:
        game = game_dir_with_version(self.root, unity_payload("0.2.53.2"))
        api = self._client(game)
        detected_melonloader(game)

        with patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False):
            self.assertTrue(api.adopt_existing()["ok"])
            self._wait_agreement(api, {LOADER_ID})
            result = api.remove_modloader(LOADER_ID)

        self.assertTrue(result["ok"], result)
        self._wait_agreement(api, set())

    def test_disabling_a_mod_is_reported_the_same_way_everywhere(self) -> None:
        """禁用之后：扫描行、安装记录、未识别列表说的是同一件事；再启用要回到启用。"""
        game = game_dir_with_version(self.root, unity_payload("0.2.53.2"))
        detected_melonloader(game)
        (game / "Mods").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURE_MOD, game / "Mods" / "FixtureMod.dll")
        api = self._client(game)

        with patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False):
            self.assertTrue(api.adopt_existing()["ok"])
        self._wait(api, "installed", lambda value: [
            mod["path"] for mod in value["local_mods"]
        ] == ["Mods/FixtureMod.dll"])

        disabled = api.toggle_mod("Mods/FixtureMod.dll", False)
        self.assertTrue(disabled["ok"], disabled)

        self._wait(api, "installed", lambda value: self._disabled_state(value) == (
            "Mods/FixtureMod.dll.disable",
            True,
        ))
        self.assertEqual(
            self._record_files(api),
            ["Mods/FixtureMod.dll"],
            "记录记的是规范路径（`.dll` / `.dll.disable` 是同一个逻辑文件），禁用只翻标志",
        )
        self.assertIn(
            "fixture.sprocket-mod",
            [item["id"] for item in (api.data.get("installed") or {}).get("installed", [])],
            "禁用不许把归属弄丢（应该仍然是一条已安装记录）",
        )
        self.assertEqual(
            [item["path"] for item in (api.data.get("installed") or {}).get("unrecognized", [])],
            [],
            "它有归属，不许同时出现在未识别里",
        )

        enabled = api.toggle_mod("Mods/FixtureMod.dll.disable", True)
        self.assertTrue(enabled["ok"], enabled)
        self._wait(api, "installed", lambda value: self._disabled_state(value) == (
            "Mods/FixtureMod.dll",
            False,
        ))

    @staticmethod
    def _disabled_state(value: dict) -> tuple[str, bool]:
        """已安装那份读数里唯一那个模组的（路径, 是不是被禁用了）。"""
        mods = value.get("local_mods") or []
        if len(mods) != 1:
            return ("", False)
        return (str(mods[0]["path"]), bool(mods[0]["disabled"]))

    def _record_files(self, api: ClientApi) -> list[str]:
        return sorted(
            str(path)
            for item in (api.data.get("installed") or {}).get("installed", [])
            for path in item.get("files", ())
        )

    def test_switching_the_game_directory_leaves_no_old_reading_anywhere(self) -> None:
        """换目录之后上一个目录的读数一个都不许残留：这是"混读"那道门的验收。"""
        first = game_dir_with_version(self.root / "first", unity_payload("0.2.53.2"))
        second = game_dir_with_version(self.root / "second", unity_payload("0.2.55.5"))
        api = self._client(first)
        detected_melonloader(first)

        with patch("sprocket_mod_manager.infrastructure.installer.sprocket_is_running", return_value=False):
            self.assertTrue(api.adopt_existing()["ok"])
            self._wait_agreement(api, {LOADER_ID})

        # 在本机设置里换目录：数据层自己作废上一个目录的读数并重取。
        saved = api.save_settings({"language": "zh", "game_path": str(second), "index_url": ""})
        self.assertTrue(saved["ok"], saved)

        self._wait(api, "environment", lambda value: (
            (value.get("sprocket") or {}).get("version") == "0.2.55.5"
        ))
        self._wait_agreement(api, set())
        self.assertEqual(
            (api.data.get("environment") or {}).get("sprocket", {}).get("version"),
            "0.2.55.5",
            "游戏版本必须是新目录的",
        )


if __name__ == "__main__":
    unittest.main()
