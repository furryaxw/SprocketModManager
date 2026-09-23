"""启动时的管理器更新检查：真跑一边 melonloader.js，核对弹窗给出的两条路。

- 打包成单文件 → 「立即更新」走 `apply_manager_update`（下载 + 换壳重启）；
- 源码运行 → 只能 `open_url` 去发布页，并在弹窗里说清为什么不能自助更新；
- 「稍后」→ 两个都不发，这次会话继续用（下次启动照样问）。

没有 node 时自动跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sprocket_mod_manager.domain.models import ReleaseAsset  # noqa: E402
from sprocket_mod_manager.domain.semver import Version  # noqa: E402
from sprocket_mod_manager.infrastructure.github import RepositoryRelease  # noqa: E402
from sprocket_mod_manager.presentation.web_gui import ClientApi  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "fixtures" / "client_ui" / "render_update_harness.js"
CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
NODE = shutil.which("node")

PAGE_URL = "https://github.com/furryaxw/sprocket-mods/releases/tag/v0.6.0"
EXE_URL = (
    "https://github.com/furryaxw/sprocket-mods/releases/download/v0.6.0/SprocketModManager.exe"
)


class FakeGithub:
    def __init__(self, release: RepositoryRelease) -> None:
        self.release = release
        self.repositories: list[str] = []

    def latest_repository_release(self, repository: str) -> RepositoryRelease:
        self.repositories.append(repository)
        return self.release


def manager_release(*, version: str = "0.6.0", notes: str = "修了几个崩溃") -> RepositoryRelease:
    return RepositoryRelease(
        tag=f"v{version}",
        version=Version.parse(version),
        page_url=f"https://github.com/furryaxw/sprocket-mods/releases/tag/v{version}",
        notes=notes,
        assets=(
            ReleaseAsset(id=1, name="SprocketModManager.exe", size=4096, download_url=EXE_URL,
                         digest="sha256:" + "a" * 64),
            ReleaseAsset(id=2, name="SprocketModManager.exe.sha256", size=89,
                         download_url=EXE_URL + ".sha256"),
        ),
    )



def update_payload(**overrides) -> dict:
    payload = {
        "ok": True,
        "current": "0.5.1",
        "latest": "0.6.0",
        "newer": True,
        "page_url": PAGE_URL,
        "notes": "修了几个崩溃\n加了导出",
        "can_self_update": True,
        "size": 2048,
    }
    payload.update(overrides)
    return payload


@unittest.skipIf(NODE is None, "node is not available")
class ManagerUpdateUiTests(unittest.TestCase):
    def _render(self, update: dict | None = None, **payload) -> dict:
        payload.setdefault("startup", True)
        payload.setdefault("confirm", True)
        payload.setdefault("update", update if update is not None else update_payload())
        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "payload.json"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(HARNESS), str(CLIENT_UI), str(payload_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        self.assertEqual(completed.returncode, 0, (completed.stdout or "") + (completed.stderr or ""))
        result = json.loads(completed.stdout or "{}")
        self.assertNotIn("error", result, result.get("error"))
        return result

    @staticmethod
    def _calls(result: dict) -> list[list]:
        return [entry["args"] for entry in result["apiCalls"] if entry["kind"] == "call"]

    def test_a_packaged_build_offers_to_update_itself_at_startup(self) -> None:
        result = self._render()

        self.assertEqual(result["modal"]["title"], "Manager update available")
        self.assertEqual(result["modal"]["confirmText"], "Update now")
        self.assertEqual(result["modal"]["cancelText"], "Later")
        self.assertIn("Version 0.6.0 is available", result["modal"]["body"])
        self.assertIn("修了几个崩溃", result["modal"]["body"], "发布说明也给人看")
        self.assertEqual(
            self._calls(result),
            [["get_manager_update"], ["apply_manager_update"]],
            "确认后是自更新，不是打开网页",
        )

    def test_the_about_button_triggers_the_same_self_update(self) -> None:
        """「关于」页那个按钮：有新版且能自更新时，点的就是自更新那条路。"""
        result = self._render()

        self.assertEqual(result["button"]["text"], "Update now")
        self.assertFalse(result["button"]["disabled"])
        self.assertEqual(result["latest"], "0.6.0")

    def test_a_source_run_is_sent_to_the_release_page(self) -> None:
        result = self._render(update_payload(can_self_update=False))

        self.assertEqual(result["modal"]["confirmText"], "Get update")
        self.assertIn("cannot replace itself", result["modal"]["body"])
        self.assertEqual(
            self._calls(result),
            [["get_manager_update"], ["open_url", PAGE_URL]],
            "装不了自更新就别下载，带人去发布页",
        )

    def test_later_keeps_the_manager_running_without_updating(self) -> None:
        result = self._render(confirm=False)

        self.assertEqual(
            self._calls(result), [["get_manager_update"]],
            "暂缓＝这次会话什么都不做，下次启动再问",
        )
        self.assertEqual(result["toasts"], [])

    def test_no_newer_release_means_no_prompt(self) -> None:
        result = self._render(update_payload(newer=False, latest="0.5.1"))

        self.assertIsNone(result["modal"])
        self.assertEqual(self._calls(result), [["get_manager_update"]])
        self.assertEqual(result["button"]["text"], "Up to date")
        self.assertTrue(result["button"]["disabled"])

    def test_a_manual_check_never_pops_the_dialog(self) -> None:
        """「关于」页自己点检查时只更新那两行字，不该再弹一次窗。"""
        result = self._render(startup=False)

        self.assertIsNone(result["modal"])
        self.assertEqual(self._calls(result), [["get_manager_update"]])
        self.assertEqual(result["button"]["text"], "Update now")

    def test_a_failed_download_is_reported(self) -> None:
        result = self._render(
            update_payload(),
            apply={"ok": False, "code": "update_apply_failed", "message": "boom"},
        )

        self.assertEqual(self._calls(result), [["get_manager_update"], ["apply_manager_update"]])
        self.assertEqual(len(result["toasts"]), 1)
        self.assertIn("Update failed", result["toasts"][0])
        self.assertIn("boom", result["toasts"][0], "后端原文留着排查用")


class ManagerUpdateApiTests(unittest.TestCase):
    """界面用的那两个接口：查到的字段、以及「源码运行不给自更新」的边界。"""

    def _api(self, root: Path, release: RepositoryRelease) -> ClientApi:
        service = SimpleNamespace(
            http=None, registry=None, environment=None, github=FakeGithub(release)
        )
        return ClientApi("0.5.1", app_dir=root / "app", service_factory=lambda _app_dir: service)

    def _close(self, api: ClientApi) -> None:
        api._environment_monitor.stop()
        api.install_queue.close()

    def test_the_check_reports_the_notes_and_whether_this_build_can_replace_itself(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), manager_release())
            try:
                result = api.get_manager_update()
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["current"], "0.5.1")
        self.assertEqual(result["latest"], "0.6.0")
        self.assertTrue(result["newer"])
        self.assertEqual(result["page_url"], PAGE_URL)
        self.assertEqual(result["notes"], "修了几个崩溃", "弹窗里要给人看发布说明")
        self.assertFalse(result["can_self_update"], "测试进程是源码运行")
        self.assertEqual(result["size"], 4096)

    def test_an_older_release_is_not_an_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), manager_release(version="0.5.0"))
            try:
                result = api.get_manager_update()
            finally:
                self._close(api)

        self.assertTrue(result["ok"], result)
        self.assertFalse(result["newer"])

    def test_a_source_run_refuses_to_replace_itself(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(Path(directory), manager_release())
            try:
                result = api.apply_manager_update()
            finally:
                self._close(api)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "self_update_unavailable")


if __name__ == "__main__":
    unittest.main()
