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


def install_melonloader(game: Path) -> None:
    """游戏根目录的 MelonLoader 布局：认领与扫描 `Mods` 之前得先检测到运行时。"""
    (game / "version.dll").touch()
    (game / "MelonLoader" / "net6").mkdir(parents=True, exist_ok=True)
    (game / "MelonLoader" / "net6" / "MelonLoader.dll").touch()


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
        install_melonloader(game)
        ConfigStore(app_dir).save({"language": "zh", "game_path": str(game), "index_url": ""})
        service = ModManagerService(app_dir)
        service.registry = Registry(packages)
        return ClientApi("test", app_dir=app_dir, service_factory=lambda _app_dir: service)

    def test_planning_an_installed_version_still_opens_the_plan_page(self) -> None:
        """装着的这一版照样给计划：计划页是换版本、重装的唯一入口，不能被「同版本」挡掉。"""
        item = package("test.mod", "TestMod.dll", b"content", versions=("1.0.0",))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = self._api(root, [item])
            (root / "game" / "Mods" / "TestMod.dll").write_bytes(b"content")
            try:
                api.adopt_existing()
                planned = api.plan_changes(["test.mod"], {"test.mod": "1.0.0"})
                reinstalled = api.plan_changes(["test.mod"], {"test.mod": "1.0.0"}, reinstall=True)
            finally:
                api.install_queue.close()

        self.assertTrue(planned["ok"], planned)
        self.assertEqual(planned["install"], {"test.mod": "1.0.0"})
        self.assertEqual([line["action"] for line in planned["lines"]], ["install"])
        self.assertTrue(reinstalled["ok"], reinstalled)
        self.assertTrue(reinstalled["lines"][0]["reinstall"], "重装这个标记落在那一行上")

    def test_plan_marks_the_broken_mod_red_and_keeps_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), [
                package("test.good", "Good.dll", b"good"),
                unresolvable_package(),
                package("test.lib", "Lib.dll", b"lib", versions=("1.0.0",)),
            ])
            try:
                result = api.plan_changes(["test.broken", "test.good"])
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["install"], {"test.good": "1.0.0"})
            lines = {line["id"]: line for line in result["lines"]}
            self.assertEqual(lines["test.good"]["action"], "install")
            self.assertEqual(lines["test.broken"]["action"], "skip", "解不出来的那个标红出局")
            problem = lines["test.broken"]["problems"][0]
            self.assertEqual(problem["kind"], "missing-dependency")
            self.assertIn("test.lib >=9.9.9", problem["text"], "红行里写清缺的是哪条依赖")

    def test_a_batch_that_has_nothing_to_install_is_still_a_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), [
                unresolvable_package(),
                package("test.lib", "Lib.dll", b"lib", versions=("1.0.0",)),
            ])
            try:
                result = api.plan_changes(["test.broken"])
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["install"], {}, "一个都装不了就是空安装列表")
            self.assertEqual(result["remove"], [])
            self.assertEqual([line["action"] for line in result["lines"]], ["skip"])

    def test_plan_names_a_dependency_that_is_not_in_the_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), [unresolvable_package()])
            try:
                result = api.plan_changes(["test.broken"])
            finally:
                api.install_queue.close()

            self.assertTrue(result["ok"], result)
            problem = result["lines"][0]["problems"][0]
            self.assertIn("test.lib", problem["text"])

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
                result = api.enqueue_install(["test.broken"])
            finally:
                api.install_queue.close()

            self.assertFalse(result["ok"], result)
            self.assertEqual(result["code"], "install_enqueue_failed")
            self.assertIn("test.lib >=9.9.9", result["message"])


class BatchInstallUiTests(unittest.TestCase):
    """客户端必须真的把计划里的红行用起来：每一行都有自己的框，理由写在框里。"""

    def setUp(self) -> None:
        self.client_ui = (
            Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
        )

    def test_plan_body_writes_the_reason_inside_the_mod_box(self) -> None:
        installs = (self.client_ui / "js" / "installs.js").read_text(encoding="utf-8")
        self.assertIn("function planLineBox(line, onChoice, planState)", installs)
        self.assertIn(
            'group.className = line.problems?.length ? "plan-group skipped" : "plan-group"',
            installs,
        )
        self.assertIn(
            'reason.textContent = `${tr("skippedReason")}: ${planProblemText(problem, line.id)}`',
            installs,
        )
        self.assertIn('return tr("planConflictReason", {others})', installs, "冲突的话由界面按语言写")

    def test_every_planned_package_keeps_its_box(self) -> None:
        installs = (self.client_ui / "js" / "installs.js").read_text(encoding="utf-8")
        self.assertIn(
            "for (const line of planState.plan?.lines || [])",
            installs,
            "计划里的每一行都有框，出局的那些也在",
        )
        self.assertIn("body.append(planLineBox(line, onChoice, planState))", installs)

    def test_the_plan_page_reports_what_it_did(self) -> None:
        installs = (self.client_ui / "js" / "installs.js").read_text(encoding="utf-8")
        self.assertIn('toast(tr("queued", {count: applied.count}))', installs)
        self.assertIn(
            'tr("removed", {names: (applied.removed || []).join(", ")})',
            installs,
            "卸载的完成提示要报出真卸掉的那几个",
        )

    def test_the_new_words_exist_in_both_languages(self) -> None:
        text = (self.client_ui / "js" / "i18n.js").read_text(encoding="utf-8")
        for key in ("skippedReason", "planSkipOption", "planRemoveOption", "planReinstall"):
            self.assertEqual(text.count(f"{key}:"), 2, f"{key} 的 zh 与 en 都要有")


if __name__ == "__main__":
    unittest.main()

