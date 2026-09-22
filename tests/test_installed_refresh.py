"""「已安装」页刷新路径的硬约束：只读磁盘 + 已缓存 Registry，**绝不访问网络**。

认领（adoption）会去拉 GitHub Release，所以它是独立端点 `adopt_existing`，
由前端在渲染之后再异步调用。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# unittest 既可能以顶层模块加载（discover -s tests），也可能以 tests.xxx 加载；
# 后者需要显式把 tests 目录放进 sys.path 才能复用 test_adoption 的夹具构造器。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.application.service import ModManagerService  # noqa: E402
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402
from sprocket_mod_manager.infrastructure.config import ConfigStore  # noqa: E402
from sprocket_mod_manager.presentation.web_gui import ClientApi  # noqa: E402

from test_adoption import FIXTURE_MOD, package  # noqa: E402  (复用认领测试里的包构造器)

FIXTURE_DIR = FIXTURE_MOD.parent


class ExplodingGitHub:
    """任何访问都炸——用来证明刷新路径没有网络依赖。"""

    def releases(self, *_args, **_kwargs):
        raise AssertionError("the installed page must not contact GitHub")

    def install_assets(self, *_args, **_kwargs):
        raise AssertionError("the installed page must not contact GitHub")

    def repository_readme(self, *_args, **_kwargs):
        raise AssertionError("the installed page must not contact GitHub")


class InstalledRefreshTests(unittest.TestCase):
    def _api(self, root: Path) -> tuple[ClientApi, Path, ModManagerService]:
        app_dir = root / "app"
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        shutil.copyfile(FIXTURE_MOD, game / "Mods" / "FixtureMod.dll")
        ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
        service = ModManagerService(app_dir)
        service.registry = Registry(
            [
                package(
                    "fixture.sprocket-mod",
                    "FixtureMod.dll",
                    FIXTURE_MOD.read_bytes(),
                    repository="fixture/FixtureMod",
                    digest_of=b"mismatched on purpose",
                )
            ]
        )
        api = ClientApi("0.4.2", app_dir=app_dir, service_factory=lambda _app_dir: service)
        return api, game, service

    def test_get_installed_never_touches_the_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, _game, service = self._api(Path(directory))
            service.github = ExplodingGitHub()
            try:
                result = api.get_installed()
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            self.assertEqual(len(result["local_mods"]), 1)
            self.assertEqual(result["local_mods"][0]["registry_id"], "fixture.sprocket-mod",
                             "matching still works from the cached registry alone")
            self.assertEqual(result["local_summary"]["registry_matched"], 1)

    def test_adopt_existing_is_a_separate_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, game, service = self._api(Path(directory))
            try:
                claimed = api.adopt_existing()
                listed = api.get_installed()
            finally:
                api.install_queue.close()

            self.assertTrue(claimed["ok"], claimed)
            self.assertTrue(claimed["changed"], "认领了东西就必须报告 changed")
            self.assertEqual(listed["installed"][0]["id"], "fixture.sprocket-mod")
            self.assertEqual(listed["local_mods"][0]["installed_package_id"], "fixture.sprocket-mod")
            self.assertTrue((game / "Mods" / "FixtureMod.dll").is_file())

    def test_one_refresh_requests_each_dll_metadata_once(self) -> None:
        """刷新路径不许重复扫描：一次 `get_installed` 里每个 DLL 只该请求一次元数据。

        计数的对象必须是**缓存查表层**（`local_mods.read_cached_metadata`）而不是底层解析
        （`read_dll_metadata`）——后者在内存缓存命中时不会增长，会把"同一请求里扫两遍"这种
        重复劳动掩盖过去。这条断言就是那道防线。
        """
        from sprocket_mod_manager.application import local_mods

        with tempfile.TemporaryDirectory() as directory:
            api, _game, _service = self._api(Path(directory))
            calls: list[str] = []
            real_read = local_mods.read_cached_metadata

            def counting_read(path):
                calls.append(str(path))
                return real_read(path)

            try:
                with patch.object(local_mods, "read_cached_metadata", counting_read):
                    result = api.get_installed()
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            self.assertEqual(len(result["local_mods"]), 1, "夹具目录里就一个 DLL")
            self.assertEqual(len(calls), 1, "一次刷新只该请求每个 DLL 的元数据一次")

    def test_toggling_syncs_the_install_record(self) -> None:
        """禁用/启用改名后，安装记录必须跟着走，否则管理器与自己的缓存打架。"""
        with tempfile.TemporaryDirectory() as directory:
            api, game, _service = self._api(Path(directory))
            try:
                self.assertTrue(api.adopt_existing()["ok"])
                before = api.get_installed()
                self.assertEqual(before["local_mods"][0]["installed_package_id"], "fixture.sprocket-mod")
                self.assertFalse(before["local_mods"][0]["disabled"])

                disabled = api.toggle_mod("Mods/FixtureMod.dll", False)
                after = api.get_installed()
            finally:
                api.install_queue.close()

            self.assertTrue(disabled["ok"], disabled)
            self.assertEqual(disabled["toggled"], "Mods/FixtureMod.dll.disable")
            row = after["local_mods"][0]
            self.assertEqual(row["path"], "Mods/FixtureMod.dll.disable")
            self.assertTrue(row["disabled"])
            self.assertEqual(row["installed_package_id"], "fixture.sprocket-mod",
                             "a disabled mod keeps its ownership and stays out of the local-only bucket")
            self.assertEqual(after["installed"][0]["id"], "fixture.sprocket-mod")

    def test_localized_registry_name_is_delivered_to_the_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api, _game, _service = self._api(Path(directory))
            try:
                result = api.get_installed()
            finally:
                api.install_queue.close()

            names = result["local_mods"][0]["registry_display_name"]
            self.assertEqual(names.get("en"), "fixture.sprocket-mod",
                             "the cached registry entry is shipped with the row so the UI can localize it")


if __name__ == "__main__":
    unittest.main()
