"""批量安装：一个模组解析不了，跳过它、装剩下的，并把原因带出来。

单个包失败时仍然报错（用户点的那一个不能"假装成功"），但只要还有能装的，
就返回成功 + `failed` 清单，让界面提示"这些被跳过"。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

# unittest 既可能以顶层模块加载（discover -s tests），也可能以 tests.xxx 加载。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.domain.models import RegistryPackage  # noqa: E402
from sprocket_mod_manager.domain.registry import Registry  # noqa: E402
from sprocket_mod_manager.application.service import ModManagerService  # noqa: E402
from sprocket_mod_manager.infrastructure.config import ConfigStore  # noqa: E402
from sprocket_mod_manager.presentation.web_gui import ClientApi  # noqa: E402

from test_adoption import package  # noqa: E402  (复用带内嵌 release 的包构造器)


def unresolvable_package() -> RegistryPackage:
    """依赖一个 Registry 里根本没有的版本：解析必然失败，且失败点明确。"""
    broken = package("test.broken", "Broken.dll", b"broken")
    return replace(broken, dependencies=({"id": "test.lib", "version": ">=9.9.9", "when": "*"},))


class BatchInstallTests(unittest.TestCase):
    def _api(self, root: Path, packages: list[RegistryPackage]) -> ClientApi:
        app_dir = root / "app"
        game = root / "game"
        (game / "Mods").mkdir(parents=True)
        (game / "Sprocket.exe").touch()
        ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
        service = ModManagerService(app_dir)
        service.registry = Registry(packages)
        return ClientApi("test", app_dir=app_dir, service_factory=lambda _app_dir: service)

    def test_plan_skips_the_broken_mod_and_keeps_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), [
                package("test.good", "Good.dll", b"good"),
                unresolvable_package(),
                package("test.lib", "Lib.dll", b"lib", versions=("1.0.0",)),
            ])
            try:
                result = api.plan_install(["test.broken", "test.good"])
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            self.assertEqual([plan["id"] for plan in result["plans"]], ["test.good"])
            self.assertEqual([item["id"] for item in result["failed"]], ["test.broken"])
            self.assertIn("test.lib >=9.9.9", result["failed"][0]["message"])
            self.assertIn("1.0.0", result["failed"][0]["message"], "顺带说出这个包实际有哪些版本")

    def test_plan_still_fails_when_nothing_can_be_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), [
                unresolvable_package(),
                package("test.lib", "Lib.dll", b"lib", versions=("1.0.0",)),
            ])
            try:
                result = api.plan_install(["test.broken"])
            finally:
                api.install_queue.close()

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["code"], "install_plan_failed")
            # 单条失败不带壳：界面直接看到解析器给的诊断。
            self.assertIn("test.lib >=9.9.9", result["message"])

    def test_plan_reports_a_dependency_that_is_not_in_the_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), [unresolvable_package()])
            try:
                result = api.plan_install(["test.broken"])
            finally:
                api.install_queue.close()

            self.assertFalse(result["ok"], result)
            self.assertIn("test.lib", result["message"])

    def test_enqueue_hands_over_only_the_installable_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), [
                package("test.good", "Good.dll", b"good"),
                unresolvable_package(),
                package("test.lib", "Lib.dll", b"lib", versions=("1.0.0",)),
            ])
            try:
                with patch.object(api.install_queue, "enqueue", return_value=()) as enqueue:
                    result = api.enqueue_install(
                        ["test.broken", "test.good"],
                        allow_without_melonloader=True,
                    )
            finally:
                api.install_queue.close()

            self.assertEqual(enqueue.call_args.args[0], ["test.good"], "坏的那个不进队列")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["count"], 0)
            self.assertEqual([item["id"] for item in result["failed"]], ["test.broken"])

    def test_enqueue_reports_failure_when_the_only_package_is_broken(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), [
                unresolvable_package(),
                package("test.lib", "Lib.dll", b"lib", versions=("1.0.0",)),
            ])
            try:
                result = api.enqueue_install(["test.broken"], allow_without_melonloader=True)
            finally:
                api.install_queue.close()

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["code"], "install_enqueue_failed")
            self.assertIn("test.lib >=9.9.9", result["message"])


class BatchInstallUiTests(unittest.TestCase):
    """客户端必须真的把 `failed` 用起来：装剩下的之前先说清跳过了谁。"""

    def setUp(self) -> None:
        self.client_ui = (
            Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
        )

    def test_plan_body_lists_the_skipped_mods_with_their_reason(self) -> None:
        catalog = (self.client_ui / "js" / "catalog.js").read_text(encoding="utf-8")
        self.assertIn("function createPlanBody(plans, recommendations = [], failed = [])", catalog)
        self.assertIn('tr("skippedMods", {count: failed.length})', catalog)
        self.assertIn('group.className = "plan-group skipped-group"', catalog)
        self.assertIn("reason.textContent = item.message", catalog)

    def test_install_flows_pass_failed_through_and_report_it(self) -> None:
        installs = (self.client_ui / "js" / "installs.js").read_text(encoding="utf-8")
        self.assertIn(
            "createPlanBody(result.plans, result.recommendations || [], result.failed || [])",
            installs,
        )
        self.assertEqual(
            installs.count('tr("skippedMods", {count: queued.failed.length})'),
            1,
            "批量安装的完成提示要带上跳过的数量",
        )
        self.assertIn('tr("skippedMods", {count: result.failed.length})', installs,
                      "批量更新的完成提示同样要带上跳过的数量")

    def test_skipped_message_exists_in_both_languages(self) -> None:
        text = (self.client_ui / "js" / "i18n.js").read_text(encoding="utf-8")
        self.assertEqual(text.count("skippedMods:"), 2, "zh 与 en 都要有")


if __name__ == "__main__":
    unittest.main()

