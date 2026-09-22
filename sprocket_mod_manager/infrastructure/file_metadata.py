"""DLL 元数据缓存文件：`<game>/SprocketModManager/file-metadata.json`。

安装记录只管"哪个包拥有哪些文件"；DLL 元数据缓存单独一个文件、**不含任何在线信息**。

对外只暴露 `load_sections()` / `store_sections(sections)`，与 `dll_metadata.configure_metadata_backend`
要求的后端接口一致，所以 `dll_metadata` 不需要知道它落在哪个文件里。

结构（v1，**按内容 hash 键**）：
`{"schema_version": 1, "files": {"<绝对路径>": {"size":…, "mtime":…, "hash":"…"}}, "meta": {"<hash>": {…}}}`
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class FileMetadataStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load_sections(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # 缓存坏了不是错误：丢掉重新解析即可。
            LOGGER.warning("metadata cache file is unreadable, starting empty: %s", exc)
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            return {}
        return payload

    def store_sections(self, sections: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "files": dict(sections.get("files") or {}),
            "meta": dict(sections.get("meta") or {}),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(
            (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        os.replace(temporary, self.path)
