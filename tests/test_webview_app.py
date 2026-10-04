from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from sprocket_mod_manager.presentation.webview_app import run_gui

GPU_FLAGS = "QTWEBENGINE_CHROMIUM_FLAGS"


class RunGuiBackendTests(unittest.TestCase):
    def _start_kwargs(self, platform: str, *, debug: bool = True) -> dict:
        webview = MagicMock()
        with (
            patch.dict(sys.modules, {"webview": webview}),
            patch("sprocket_mod_manager.presentation.web_gui.ClientApi"),
            patch("sprocket_mod_manager.presentation.webview_app.sys.platform", platform),
        ):
            run_gui("test", debug=debug)
        webview.start.assert_called_once()
        return webview.start.call_args.kwargs

    def test_windows_uses_webview2_with_devtools(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(GPU_FLAGS, None)
            kwargs = self._start_kwargs("win32")
            self.assertNotIn(GPU_FLAGS, os.environ)
        self.assertEqual(kwargs["gui"], "edgechromium")
        self.assertTrue(kwargs["debug"])

    def test_linux_lets_pywebview_pick_qt_without_gpu_or_devtools(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(GPU_FLAGS, None)
            kwargs = self._start_kwargs("linux")
            self.assertEqual(os.environ[GPU_FLAGS], "--disable-gpu")
        self.assertIsNone(kwargs["gui"])
        self.assertFalse(kwargs["debug"], "Qt DevTools window breaks client startup")

    def test_linux_keeps_user_chromium_flags(self) -> None:
        with patch.dict(os.environ, {GPU_FLAGS: "--use-gl=egl"}):
            self._start_kwargs("linux")
            self.assertEqual(os.environ[GPU_FLAGS], "--use-gl=egl")


if __name__ == "__main__":
    unittest.main()
