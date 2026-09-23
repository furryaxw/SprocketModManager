from __future__ import annotations

import os
from pathlib import Path

DEFAULT_INDEX_URL = "https://sprocketmods.furryaxw.top/index.json"

# 管理器自己的发布就放在注册表仓库里：tag `v<版本>`，资产 `SprocketModManager.exe`（+ `.sha256`）。
MANAGER_REPOSITORY = "furryaxw/sprocket-mods"


def default_app_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "SprocketModManager" if local else Path.home() / ".sprocket-mod-manager"
