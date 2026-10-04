from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sprocket_mod_manager.infrastructure.gpu_rendering import ATTEMPT_MARKER, DISABLED_MARKER
from sprocket_mod_manager.presentation.webview_app import run_gui

GPU_FLAGS = "QTWEBENGINE_CHROMIUM_FLAGS"
MODULE = "sprocket_mod_manager.presentation.webview_app"


class RunGuiBackendTests(unittest.TestCase):
    def _run(
        self,
        platform: str,
        app_dir: Path,
        *,
        debug: bool = True,
        disable_gpu: bool = False,
        enable_gpu: bool = False,
        fail: bool = False,
    ) -> tuple[MagicMock, dict]:
        webview = MagicMock()
        if fail:
            webview.start.side_effect = RuntimeError("could not create the GL context")
        client_api = MagicMock()
        client_api.config_store.app_dir = app_dir
        with (
            patch.dict(sys.modules, {"webview": webview}),
            patch("sprocket_mod_manager.presentation.web_gui.ClientApi", return_value=client_api),
            patch(f"{MODULE}.sys.platform", platform),
        ):
            run_gui("test", debug=debug, disable_gpu=disable_gpu, enable_gpu=enable_gpu)
        return webview, webview.start.call_args.kwargs

    def test_windows_uses_webview2_and_touches_no_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(GPU_FLAGS, None)
                webview, kwargs = self._run("win32", app_dir)
                self.assertNotIn(GPU_FLAGS, os.environ)
            self.assertEqual(kwargs["gui"], "edgechromium")
            self.assertTrue(kwargs["debug"])
            webview.settings.__setitem__.assert_not_called()
            self.assertEqual(list(app_dir.iterdir()), [])

    def test_linux_runs_on_qt_with_hardware_acceleration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(GPU_FLAGS, None)
                webview, kwargs = self._run("linux", app_dir)
                self.assertNotIn(GPU_FLAGS, os.environ, "默认不关硬件加速")
            self.assertEqual(kwargs["gui"], "qt")
            self.assertTrue(kwargs["debug"])
            webview.settings.__setitem__.assert_called_once_with("OPEN_DEVTOOLS_IN_DEBUG", False)
            self.assertTrue((app_dir / ATTEMPT_MARKER).is_file(), "试 GPU 期间留个进行中的标记")
            self.assertFalse((app_dir / DISABLED_MARKER).exists())

    def test_linux_uses_software_rendering_when_the_flag_asks_for_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(GPU_FLAGS, None)
                self._run("linux", app_dir, disable_gpu=True)
                self.assertEqual(os.environ[GPU_FLAGS], "--disable-gpu")
            self.assertFalse((app_dir / ATTEMPT_MARKER).exists())
            self.assertFalse((app_dir / DISABLED_MARKER).exists(), "一次性开关不写状态")

    def test_linux_uses_software_rendering_after_a_recorded_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            (app_dir / DISABLED_MARKER).touch()
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(GPU_FLAGS, None)
                self._run("linux", app_dir)
                self.assertEqual(os.environ[GPU_FLAGS], "--disable-gpu")
            self.assertFalse((app_dir / ATTEMPT_MARKER).exists())

    def test_linux_tries_hardware_again_with_the_enable_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            (app_dir / DISABLED_MARKER).touch()
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(GPU_FLAGS, None)
                self._run("linux", app_dir, enable_gpu=True)
                self.assertNotIn(GPU_FLAGS, os.environ)
            self.assertFalse((app_dir / DISABLED_MARKER).exists())
            self.assertTrue((app_dir / ATTEMPT_MARKER).is_file())

    def test_linux_keeps_user_chromium_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {GPU_FLAGS: "--use-gl=egl"}):
                self._run("linux", Path(directory), disable_gpu=True)
                self.assertEqual(os.environ[GPU_FLAGS], "--use-gl=egl")

    def test_a_failed_hardware_start_is_recorded_and_restarted_on_software(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app_dir = Path(directory)
            with patch(f"{MODULE}._restart_in_place") as restart:
                webview, _kwargs = self._run("linux", app_dir, fail=True)

            webview.start.assert_called_once()
            restart.assert_called_once()
            self.assertTrue((app_dir / DISABLED_MARKER).is_file(), "下次启动直接软件渲染")
            self.assertFalse((app_dir / ATTEMPT_MARKER).exists())

    def test_a_failed_software_start_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(f"{MODULE}._restart_in_place") as restart,
                self.assertRaises(RuntimeError),
            ):
                self._run("linux", Path(directory), disable_gpu=True, fail=True)

            restart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
