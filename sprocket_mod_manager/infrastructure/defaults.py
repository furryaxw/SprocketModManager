from __future__ import annotations

import os
from pathlib import Path

DEFAULT_INDEX_URL = "https://sprocketmods.furryaxw.top/index.json"


def default_app_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "SprocketModManager" if local else Path.home() / ".sprocket-mod-manager"
