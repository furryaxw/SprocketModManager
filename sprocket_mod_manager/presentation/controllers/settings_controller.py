from __future__ import annotations

import logging
import threading
from typing import Any

from .base import ApiController
from ..api_constants import MANAGER_REPOSITORY_URL, REGISTRY_WEBSITE_URL
from ...infrastructure.defaults import DEFAULT_INDEX_URL
from ...infrastructure.app_logging import set_logging_level
from ...infrastructure.config import (
    DEFAULT_GITHUB_PROXY_URL,
    DEFAULT_PROXY_URL,
    detect_game_path,
    effective_game_path,
    effective_github_proxy_url,
    effective_proxy_url,
)
from ...infrastructure.melonloader import MELONLOADER_REPOSITORY
from ...utilities.ui_values import normalize_text_scale
from ...utilities.urls import normalize_github_proxy_url, normalize_proxy_url

LOGGER = logging.getLogger(__name__)


class SettingsController(ApiController):
    def bootstrap(self) -> dict[str, Any]:
        LOGGER.debug("bootstrap entered")
        self.config = self.config_store.load()
        # Do not put GitHub validation, Gist sync, or private-server reconnects
        # on the WebView/API thread. The local configuration is sufficient to
        # render the window while this worker refreshes the saved login state.
        if self._github_token():
            LOGGER.info("starting background GitHub login refresh")
            threading.Thread(
                target=self._background_github_refresh,
                name="sprocket-github-login-refresh",
                daemon=True,
            ).start()
        LOGGER.debug("bootstrap returning")
        return self._success(
            version=self.version,
            language=self.language,
            settings=self._settings_data(),
            developer_servers=self._developer_servers_data(load_packages=False),
            links={
                "repository": MANAGER_REPOSITORY_URL,
                "registry": REGISTRY_WEBSITE_URL,
                "melonloader": f"https://github.com/{MELONLOADER_REPOSITORY}",
            },
        )

    def startup_trace(self, message: str) -> dict[str, Any]:
        LOGGER.debug("frontend: %s", str(message)[:300])
        return self._success()

    def client_log(self, level: str, message: str) -> dict[str, Any]:
        levels = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
        }
        normalized = str(level).casefold()
        if normalized not in levels:
            return self._failure(ValueError("unsupported log level"), code="invalid_log_level")
        LOGGER.log(levels[normalized], "frontend: %s", str(message)[:1000])
        return self._success()

    def _settings_data(self) -> dict[str, Any]:
        return {
            "debug": self.config.get("debug") is True,
            "debug_active": self._debug_override or self.config.get("debug") is True,
            "language": str(self.config.get("language", "auto")),
            "game_path": str(self.config.get("game_path", "") or ""),
            "index_url": str(self.config.get("index_url", "") or ""),
            "index_placeholder": DEFAULT_INDEX_URL,
            "proxy_enabled": self.config.get("proxy_enabled") is True,
            "proxy_url": str(self.config.get("proxy_url", "") or ""),
            "proxy_placeholder": DEFAULT_PROXY_URL,
            "github_proxy_enabled": self.config.get("github_proxy_enabled") is True,
            "github_proxy_url": str(self.config.get("github_proxy_url", "") or ""),
            "github_proxy_placeholder": DEFAULT_GITHUB_PROXY_URL,
            "game_path_placeholder": "",
            "text_scale": normalize_text_scale(self.config.get("text_scale")),
            "github_user_id": str(self.config.get("github_user_id", "") or ""),
            "developer_servers": list(self.config.get("developer_servers", [])),
            "github_gist_id": str(self.config.get("github_gist_id", "") or ""),
        }

    def get_settings(self) -> dict[str, Any]:
        self.config = self.config_store.load()
        return self._success(settings=self._settings_data(), language=self.language)

    def find_game_path(self) -> dict[str, Any]:
        try:
            return self._success(path=detect_game_path())
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="game_path_detection_failed")

    def choose_game_path(self) -> dict[str, Any]:
        if self._window is None:
            return self._failure(RuntimeError("window is not ready"))
        try:
            import webview

            current = effective_game_path(self.config)
            result = self._window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=current or "",
                allow_multiple=False,
            )
            selected = result[0] if result else ""
            return self._success(path=selected)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc, code="file_dialog_failed")

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        try:
            language = str(values.get("language", "auto"))
            if language not in {"auto", "zh", "en"}:
                raise ValueError("unsupported interface language")
            self.config = {
                "debug": values.get("debug") is True,
                "language": language,
                "game_path": str(values.get("game_path", "")).strip(),
                "index_url": str(values.get("index_url", "")).strip(),
                "proxy_enabled": values.get("proxy_enabled") is True,
                "proxy_url": normalize_proxy_url(values.get("proxy_url", "")),
                "github_proxy_enabled": values.get("github_proxy_enabled") is True,
                "github_proxy_url": normalize_github_proxy_url(
                    values.get("github_proxy_url", "")
                ),
                "text_scale": normalize_text_scale(values.get("text_scale")),
                "github_user_id": str(self.config.get("github_user_id", "") or ""),
                "developer_servers": list(self.config.get("developer_servers", [])),
                "github_gist_id": str(self.config.get("github_gist_id", "") or ""),
            }
            self.config_store.save(self.config)
            set_logging_level(self._debug_override or self.config["debug"])
            LOGGER.info(
                "debug configuration saved configured=%s override=%s active=%s",
                self.config["debug"],
                self._debug_override,
                self._debug_override or self.config["debug"],
            )
            self._configure_service_network(self.service)
            melonloader_http = getattr(self.melonloader, "http", None)
            configure = getattr(melonloader_http, "configure_network", None)
            if configure is not None:
                configure(
                    effective_proxy_url(self.config),
                    effective_github_proxy_url(self.config),
                )
            return self._success(settings=self._settings_data(), language=self.language)
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="settings_save_failed")
