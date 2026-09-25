"""侧栏日志来源对话框：静态接线、两种语言的键，以及用 Node 真实执行对话框渲染。"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "fixtures" / "client_ui" / "render_logs_harness.js"
CLIENT_UI = Path(__file__).resolve().parent.parent / "sprocket_mod_manager" / "presentation" / "client_ui"
NODE = shutil.which("node")

I18N_KEYS = (
    "uploadLogs",
    "logPickerTitle",
    "logSourceLoader",
    "logNoLoader",
    "uploadLoaderLogConfirmMessage",
    "uploadManagerLog",
    "uploadManagerLogConfirmMessage",
)


def manager_source() -> dict:
    return {
        "id": "manager",
        "kind": "manager",
        "loader": "",
        "path": "C:\\Users\\player\\AppData\\Local\\SprocketModManager\\Latest.log",
        "available": True,
    }


def loader_source(source_id: str, loader: str, log_path: str, *, available: bool) -> dict:
    return {
        "id": source_id,
        "kind": "loader",
        "loader": loader,
        "path": log_path,
        "available": available,
    }


class LogPickerMarkupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (CLIENT_UI / "index.html").read_text(encoding="utf-8")
        self.i18n = (CLIENT_UI / "js" / "i18n.js").read_text(encoding="utf-8")

    def test_the_sidebar_carries_the_upload_button_below_the_language_control(self) -> None:
        self.assertIn('id="upload-logs"', self.html)
        self.assertIn('aria-haspopup="dialog"', self.html)
        self.assertLess(
            self.html.index('id="language-select"'), self.html.index('id="upload-logs"')
        )
        self.assertLess(
            self.html.index('id="upload-logs"'), self.html.index('id="registry-state"')
        )

    def test_the_about_page_has_no_upload_button(self) -> None:
        self.assertNotIn('id="upload-manager-log"', self.html)

    def test_the_log_script_is_loaded(self) -> None:
        self.assertIn('src="./js/logs.js"', self.html)

    def test_log_picker_keys_exist_in_both_languages(self) -> None:
        for key in I18N_KEYS:
            self.assertEqual(self.i18n.count(f"{key}:"), 2, f"{key} 要有 zh 与 en")


@unittest.skipIf(NODE is None, "node is not available")
class LogPickerRenderHarnessTests(unittest.TestCase):
    def _render(self, **payload) -> dict:
        payload.setdefault("sources", [])
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
            return json.loads(completed.stdout or "{}")

    def test_the_dialog_lists_the_manager_log_then_available_loader_logs(self) -> None:
        result = self._render(sources=[
            manager_source(),
            loader_source("melonloader", "MelonLoader", "MelonLoader/Latest.log", available=True),
            loader_source("bepinex", "BepInEx", "BepInEx/LogOutput.log", available=False),
        ])

        self.assertIsNone(result["error"])
        self.assertEqual(result["pickerTitle"], "Choose a log to upload")
        self.assertEqual(result["items"], ["Upload manager log", "MelonLoader log"])
        self.assertFalse(result["emptyShown"])
        self.assertEqual(result["entries"][0]["className"], "log-picker-item")

    def test_only_the_manager_log_says_there_is_no_loader_log(self) -> None:
        result = self._render(sources=[
            manager_source(),
            loader_source("melonloader", "MelonLoader", "MLLoader/MelonLoader/Latest.log", available=False),
        ])

        self.assertEqual(result["items"], ["Upload manager log"])
        self.assertTrue(result["emptyShown"])

    def test_the_picker_offers_only_a_close_button(self) -> None:
        result = self._render(sources=[manager_source()])

        self.assertIsNone(result["pickerCancel"])

    def test_clicking_a_loader_entry_uploads_that_source_after_the_confirm_step(self) -> None:
        result = self._render(
            sources=[
                manager_source(),
                loader_source("melonloader", "MelonLoader", "MelonLoader/Latest.log", available=True),
            ],
            click=1,
        )

        calls = [entry["args"] for entry in result["apiCalls"] if entry.get("kind") == "call"]
        self.assertIn(["upload_log", "melonloader"], calls)
        self.assertEqual(result["modals"][:3], ["Choose a log to upload", "Upload log", "Log uploaded"])

    def test_a_declined_confirmation_does_not_upload(self) -> None:
        result = self._render(
            sources=[
                manager_source(),
                loader_source("melonloader", "MelonLoader", "MelonLoader/Latest.log", available=True),
            ],
            click=0,
            confirm=False,
        )

        calls = [entry["args"] for entry in result["apiCalls"] if entry.get("kind") == "call"]
        self.assertNotIn(["upload_log", "manager"], calls)
        self.assertEqual(result["modals"], ["Choose a log to upload", "Upload log"])

    def test_the_manager_entry_uploads_the_manager_source(self) -> None:
        result = self._render(sources=[manager_source()], click=0)

        calls = [entry["args"] for entry in result["apiCalls"] if entry.get("kind") == "call"]
        self.assertIn(["upload_log", "manager"], calls)


if __name__ == "__main__":
    unittest.main()
