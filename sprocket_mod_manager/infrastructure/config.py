from __future__ import annotations

import json
import locale
import logging
import os
from pathlib import Path
from typing import Any

from .defaults import DEFAULT_INDEX_URL, default_app_dir
from .steam_locator import detect_game_path
from ..utilities.ui_values import DEFAULT_TEXT_SCALE, normalize_text_scale
from ..utilities.urls import normalize_github_proxy_url, normalize_proxy_url

DEFAULT_PROXY_URL = "http://127.0.0.1:7890"
DEFAULT_GITHUB_PROXY_URL = "https://gh-proxy.com/"
LOGGER = logging.getLogger(__name__)


def language_from_locale_name(value: str | None) -> str:
    normalized = (value or "").casefold().replace("_", "-")
    return "zh" if normalized.startswith("zh") or "chinese" in normalized else "en"


def _windows_user_locale() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
            return buffer.value
    except (AttributeError, OSError, ValueError):
        pass
    return ""


def detect_language() -> str:
    names: list[str] = []
    try:
        names.append(locale.getlocale()[0] or "")
    except (ValueError, TypeError):
        pass
    windows_locale = _windows_user_locale()
    if windows_locale:
        names.append(windows_locale)
    return "zh" if any(language_from_locale_name(name) == "zh" for name in names) else "en"


def _configured_text(config: dict[str, Any], key: str) -> str:
    value = config.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def effective_game_path(config: dict[str, Any]) -> str:
    return _configured_text(config, "game_path") or detect_game_path()


def effective_index_url(config: dict[str, Any]) -> str:
    return _configured_text(config, "index_url") or DEFAULT_INDEX_URL


def effective_proxy_url(config: dict[str, Any]) -> str:
    if config.get("proxy_enabled") is not True:
        return ""
    return normalize_proxy_url(config.get("proxy_url", "")) or DEFAULT_PROXY_URL


def effective_github_proxy_url(config: dict[str, Any]) -> str:
    if config.get("github_proxy_enabled") is not True:
        return ""
    return (
            normalize_github_proxy_url(config.get("github_proxy_url", ""))
            or DEFAULT_GITHUB_PROXY_URL
    )


class ConfigStore:
    def __init__(self, app_dir: Path | None = None):
        self.app_dir = app_dir or default_app_dir()
        self.path = self.app_dir / "config.json"

    def load(self) -> dict[str, Any]:
        LOGGER.debug("loading configuration path=%s", self.path)
        defaults: dict[str, Any] = {
            "debug": False,
            "language": "auto",
            "game_path": "",
            "index_url": "",
            "proxy_enabled": False,
            "proxy_url": "",
            "github_proxy_enabled": False,
            "github_proxy_url": "",
            "text_scale": DEFAULT_TEXT_SCALE,
            "github_user_id": "",
            "github_gist_id": "",
            "developer_servers": [],
        }
        if not self.path.is_file():
            LOGGER.info("configuration does not exist; using defaults path=%s", self.path)
            return defaults
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("configuration could not be read; using defaults path=%s error=%s", self.path, exc)
            return defaults
        if isinstance(data, dict):
            defaults.update({key: value for key, value in data.items() if isinstance(key, str)})
        defaults["text_scale"] = normalize_text_scale(defaults["text_scale"])
        if not isinstance(defaults.get("developer_servers"), list):
            defaults["developer_servers"] = []
        defaults["developer_servers"] = [
            dict(server)
            for server in defaults["developer_servers"]
            if isinstance(server, dict)
        ]
        # 抑制名单**不属于**管理器配置：它在游戏目录的 `SprocketModManager/suppression.json`
        # （见 suppression_store，条目形如 `<package id>:<文件名>`）。
        return defaults

    def save(self, config: dict[str, Any]) -> None:
        LOGGER.info("saving configuration path=%s", self.path)
        forbidden = {
            "access_token",
            "github_access_token",
            "session_token",
            "activation_key",
            "download_url",
        }

        def without_secrets(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: without_secrets(item)
                    for key, item in value.items()
                    if str(key).casefold() not in forbidden
                }
            if isinstance(value, list):
                return [without_secrets(item) for item in value]
            return value

        sanitized = without_secrets(config)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        LOGGER.debug("configuration saved keys=%s", sorted(sanitized))
