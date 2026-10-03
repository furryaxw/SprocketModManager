from __future__ import annotations

import os
from pathlib import Path

# 索引目录：客户端从这里分别取 packages.json、environment.json 与 diagnosis.json。
DEFAULT_INDEX_URL = "https://sprocketmods.furryaxw.top/data"

# 管理器自己的发布就放在注册表仓库里：tag `v<版本>`，资产 `SprocketModManager.exe`（+ `.sha256`）。
MANAGER_REPOSITORY = "furryaxw/SprocketModManager"


def default_app_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "SprocketModManager" if local else Path.home() / ".sprocket-mod-manager"
