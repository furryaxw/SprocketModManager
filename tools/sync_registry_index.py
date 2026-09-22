"""把仓库里的本地 `index.json` 同步成线上发布的那份（避免本地副本过期误导工具/CLI）。

管理器默认走 `DEFAULT_INDEX_URL`；仓库里这份文件是 `gen-index.py` 的产物、且被 .gitignore 忽略，
只服务 `modman.py --index-file` 与诊断工具——所以它有义务和线上保持一致，否则就会出现
"本地文件里没有某个包 ⇒ 误判它没被收录"这种情况。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
DEFAULT_INDEX_URL = "https://sprocketmods.furryaxw.top/index.json"
TARGET = REPO / "index.json"
EXPECTED = "furryaxw.sprocket-mod-api"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    request = Request(DEFAULT_INDEX_URL, headers={"User-Agent": "sprocket-mod-manager/sync-index"})
    with urlopen(request, timeout=60) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))

    ids = [str(item.get("id", "")) for item in payload.get("packages", [])]
    before = []
    if TARGET.is_file():
        try:
            old = json.loads(TARGET.read_text(encoding="utf-8"))
            before = [str(item.get("id", "")) for item in old.get("packages", [])]
        except (OSError, ValueError):
            before = []

    TARGET.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    added = sorted(set(ids) - set(before))
    removed = sorted(set(before) - set(ids))
    print(f"generated_at(untrusted data): {payload.get('generated_at')}")
    print(f"packages: {len(before)} -> {len(ids)}")
    print(f"新增: {added or '（无）'}")
    print(f"移除: {removed or '（无）'}")
    print(f"包含 {EXPECTED}: {EXPECTED in ids}")
    print(f"written: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
