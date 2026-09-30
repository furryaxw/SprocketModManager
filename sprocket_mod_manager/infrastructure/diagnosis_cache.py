"""同步下来的诊断规则包的本地缓存。

规则包只从注册表来：索引里那份是权威，读到之后写在这里，下次启动（索引还没拉下来时）
先用缓存那份。本地不放内置副本 —— 规则会随平台变化，写死一份只会让客户端和注册表各说各话。

放在管理器的缓存目录（`<app_dir>/cache/`）而不是游戏目录：它描述的是平台，不是某个游戏目录。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..domain.diagnosis import diagnosis_pack

LOGGER = logging.getLogger(__name__)

CACHE_DIR_NAME = "cache"
CACHE_FILE_NAME = "diagnosis.json"


def diagnosis_cache_path(app_dir: Path | str) -> Path:
    return Path(app_dir) / CACHE_DIR_NAME / CACHE_FILE_NAME


def read_diagnosis_pack(app_dir: Path | str) -> dict[str, Any] | None:
    """上次同步下来的规则包；没有可用条目、文件坏了、读不到都返回 None。"""
    path = diagnosis_cache_path(app_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("diagnosis pack cache is unreadable (%s): %s", path, exc)
        return None
    pack = diagnosis_pack(payload)
    return pack if pack["entries"] else None


def write_diagnosis_pack(app_dir: Path | str, pack: Any) -> bool:
    """把索引里那份包原子写进缓存；没有条目就不动缓存（空的不代表平台事实被撤销）。"""
    normalized = diagnosis_pack(pack)
    if not normalized["entries"]:
        return False
    path = diagnosis_cache_path(app_dir)
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
        return True
    except OSError as exc:
        LOGGER.warning("cannot write diagnosis pack cache (%s): %s", path, exc)
        temporary.unlink(missing_ok=True)
        return False
