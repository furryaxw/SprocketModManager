"""看一眼 `file-metadata.json` 会不会无限增长。

用法：
    python tools/metadata_cache_stats.py <game_dir>

打印 `files` / `meta` 两个段的大小、文件字节数，以及**有多少条已经可以清理**
（路径不存在、或 hash 没人引用）。扫描结束时 `flush_metadata_cache()` 会自动清掉它们。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sprocket_mod_manager.infrastructure.manager_paths import file_metadata_path  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    game_dir = Path(argv[1]).expanduser()
    path = file_metadata_path(game_dir)
    if not path.is_file():
        print(f"no cache at {path}")
        return 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files") or {}
    meta = payload.get("meta") or {}
    missing = [name for name in files if not Path(name).exists()]
    referenced = {str(entry.get("hash")) for entry in files.values() if isinstance(entry, dict) and entry.get("hash")}
    orphan_meta = [digest for digest in meta if str(digest) not in referenced]

    print(f"path            {path}")
    print(f"bytes           {path.stat().st_size}")
    print(f"files entries   {len(files)}  (路径不存在: {len(missing)})")
    print(f"meta entries    {len(meta)}  (没被引用: {len(orphan_meta)})")
    for name in missing[:5]:
        print(f"  missing  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
