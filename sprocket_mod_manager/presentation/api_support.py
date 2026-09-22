from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from ..domain.models import ReleaseInfo
from ..infrastructure.config import effective_index_url


class GamePathRequiredError(ValueError):
    pass


def startup_trace(message: str) -> None:
    logging.getLogger("sprocket_mod_manager.startup").debug(message)


def ui_directory() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    bundled = bundle_root / "sprocket_mod_manager" / "presentation" / "client_ui"
    if bundled.is_dir():
        return bundled
    return Path(__file__).resolve().with_name("client_ui")


def app_icon_path() -> Path | None:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    icon = bundle_root / "resources" / "app-icon.ico"
    return icon if icon.is_file() else None


def source_from_config(config: dict[str, Any]) -> str | Path:
    source = effective_index_url(config)
    path = Path(source).expanduser()
    return path if path.is_file() else source


def release_data(release: ReleaseInfo | None) -> dict[str, Any] | None:
    if release is None:
        return None
    return {
        "tag": release.tag,
        "version": str(release.version),
        "published_at": release.published_at,
        "page_url": release.page_url,
        "assets": [
            {"name": asset.name, "size": asset.size}
            for asset in release.assets
        ],
    }
