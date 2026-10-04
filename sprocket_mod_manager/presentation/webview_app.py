from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .api_support import app_icon_path, ui_directory
from ..infrastructure.gpu_rendering import mark_attempt, record_failure, software_rendering

if TYPE_CHECKING:
    from .web_gui import ClientApi

LOGGER = logging.getLogger(__name__)


def run_gui(
        version: str,
        *,
        debug: bool = False,
        debug_override: bool = False,
        disable_gpu: bool = False,
        enable_gpu: bool = False,
) -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("pywebview is required for the desktop client") from exc

    # Import here so headless API users do not load the desktop host.
    from .web_gui import ClientApi

    LOGGER.info("desktop host starting version=%s debug=%s", version, debug)
    index_path = ui_directory() / "index.html"
    if not index_path.is_file():
        LOGGER.error("client UI is missing path=%s", index_path)
        raise RuntimeError(f"client UI is missing: {index_path}")

    LOGGER.debug("creating ClientApi")
    api: ClientApi = ClientApi(version, debug_override=debug_override)
    LOGGER.debug("creating WebView window")
    window = webview.create_window(
        "SprocketModManager",
        url=index_path.resolve().as_uri(),
        js_api=api,
        width=1240,
        height=780,
        min_size=(960, 640),
        background_color="#101213",
        text_select=True,
    )
    if window is None:
        LOGGER.error("WebView window creation returned no window")
        raise RuntimeError("failed to create the client window")
    api.bind_window(window)
    LOGGER.info("starting WebView2")
    window.events.closing += api.on_closing
    window.events.closed += api.on_closed
    icon_path = app_icon_path()

    app_dir = Path(api.config_store.app_dir)
    windows = sys.platform == "win32"
    gpu_attempt: Path | None = None
    if windows:
        gui = "edgechromium"
    else:
        # Pick Qt explicitly: pywebview tries GTK first on Linux, and these requirements only
        # install Qt.
        gui = "qt"
        if software_rendering(app_dir, force_off=disable_gpu, force_on=enable_gpu):
            os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
        else:
            gpu_attempt = mark_attempt(app_dir)
        # Qt shows DevTools as a separate window that blocks the client at startup, while the
        # remote debugging port stays available.
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False

    def gpu_path_works() -> None:
        """UI 起来了（或这次正常退出）：硬件加速这条路没崩，删掉进行中的标记。"""
        if gpu_attempt is not None:
            gpu_attempt.unlink(missing_ok=True)

    def webview_started() -> None:
        LOGGER.debug("WebView2 start callback entered")

    window.events.loaded += gpu_path_works
    window.events.closed += gpu_path_works
    try:
        webview.start(
            func=webview_started,
            gui=gui,
            debug=debug,
            http_server=True,
            private_mode=False,
            storage_path=str(app_dir / "webview"),
            icon=str(icon_path) if icon_path else None,
        )
    except Exception:
        if gpu_attempt is None:
            raise
        # 硬件加速这条路起不来：记下来，换软件渲染把这次启动重开。
        record_failure(app_dir)
        LOGGER.exception("hardware acceleration failed; restarting with software rendering")
        for handler in logging.getLogger().handlers:
            handler.flush()
        _restart_in_place()
    LOGGER.info("WebView2 stopped")


def _restart_in_place() -> None:
    """原样重开自己（不返回）。"""
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
