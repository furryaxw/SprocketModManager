"""「抑制损坏提示」名单：`<game>/SprocketModManager/suppression.json`。

为什么放在**游戏目录**而不是 AppData 的 `config.json`：抑制名单描述的是"这台机器上这个游戏目录里
的哪些文件"，跟安装记录、DLL 元数据缓存一样属于**游戏目录的状态**。规则是
「AppData 只放管理器自身的配置」，所以它跟状态文件待在一起：换机/整体备份游戏目录时跟着走。

结构（v1）：`{"schema_version": 1, "suppressed": ["<package id>:<文件>", "Mods/X.dll"]}`。
条目两种形式（见 `application/integrity.suppression_key`）：已归属的文件用身份，无归属的用规范相对路径。
**没有兼容层**：格式就是这一种，读到不认识的内容一律当空（发布前不做迁移）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class SuppressionStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[str]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            LOGGER.warning("suppression file is unreadable, starting empty: %s", exc)
            return []
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            return []
        entries = payload.get("suppressed")
        if not isinstance(entries, list):
            return []
        return [item for item in entries if isinstance(item, str) and item.strip()]

    def save(self, entries) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "suppressed": sorted(dict.fromkeys(str(item) for item in entries if str(item).strip())),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        os.replace(temporary, self.path)


def store_for(game_dir: Path) -> SuppressionStore:
    from .manager_paths import suppression_path

    return SuppressionStore(suppression_path(game_dir))
