"""同步下来的环境表（加载器 ↔ 游戏）的本地缓存。

这张表只从注册表来：索引里那份是权威，读到之后写在这里，下次启动（索引还没拉下来时）
先用缓存那份。本地不放内置副本 —— 平台事实会变，写死一份只会让客户端和注册表各说各话。

放在管理器的缓存目录（`<app_dir>/cache/`）而不是游戏目录：它描述的是平台，不是某个游戏目录。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..domain.compatibility import environment_table

LOGGER = logging.getLogger(__name__)

CACHE_DIR_NAME = "cache"
CACHE_FILE_NAME = "environment.json"


def environment_cache_path(app_dir: Path | str) -> Path:
    return Path(app_dir) / CACHE_DIR_NAME / CACHE_FILE_NAME


def read_environment_table(app_dir: Path | str) -> dict[str, Any] | None:
    """上次同步下来的表；没有可用条目、文件坏了、读不到都返回 None。"""
    path = environment_cache_path(app_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("environment table cache is unreadable (%s): %s", path, exc)
        return None
    table = environment_table(payload)
    return table if table["entries"] else None


def write_environment_table(app_dir: Path | str, table: Any) -> bool:
    """把索引里那份表原子写进缓存；没有条目就不动缓存（空的不代表平台事实被撤销）。"""
    normalized = environment_table(table)
    if not normalized["entries"]:
        return False
    path = environment_cache_path(app_dir)
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
        return True
    except OSError as exc:
        LOGGER.warning("cannot write environment table cache (%s): %s", path, exc)
        temporary.unlink(missing_ok=True)
        return False
