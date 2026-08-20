from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .api_support import app_icon_path, ui_directory

if TYPE_CHECKING:
    from .web_gui import ClientApi

LOGGER = logging.getLogger(__name__)


def run_gui(version: str, *, debug: bool = False, debug_override: bool = False) -> None:
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
        "Sprocket Mod Manager",
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

    def webview_started() -> None:
        LOGGER.debug("WebView2 start callback entered")

    webview.start(
        func=webview_started,
        gui="edgechromium",
        debug=debug,
        http_server=True,
        private_mode=False,
        storage_path=str(api.config_store.app_dir / "webview"),
        icon=str(icon_path) if icon_path else None,
    )
    LOGGER.info("WebView2 stopped")
