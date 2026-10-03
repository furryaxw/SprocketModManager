"""把仓库里的本地索引同步成线上发布的那三份（避免本地副本过期误导工具/CLI）。

管理器默认走 `DEFAULT_INDEX_URL`；仓库 `site/data/` 下这三份文件是 `gen-index.py` 的产物、且被
.gitignore 忽略，只服务 `modman.py --index-dir` 与诊断工具——所以它们有义务和线上保持一致，否则就会
出现"本地文件里没有某个包 ⇒ 误判它没被收录"这种情况。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
BASE_URL = "https://sprocketmods.furryaxw.top/data"
FILES = ("packages.json", "environment.json", "diagnosis.json")
TARGET = REPO / "site" / "data"
EXPECTED = "furryaxw.sprocket-mod-api"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fetch(name: str) -> dict:
    request = Request(
        f"{BASE_URL}/{name}", headers={"User-Agent": "sprocket-mod-manager/sync-index"}
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    payloads = {name: fetch(name) for name in FILES}
    ids = [str(item.get("id", "")) for item in payloads["packages.json"].get("packages", [])]
    before: list[str] = []
    previous = TARGET / "packages.json"
    if previous.is_file():
        try:
            old = json.loads(previous.read_text(encoding="utf-8"))
            before = [str(item.get("id", "")) for item in old.get("packages", [])]
        except (OSError, ValueError):
            before = []

    TARGET.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (TARGET / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    added = sorted(set(ids) - set(before))
    removed = sorted(set(before) - set(ids))
    print(f"generated_at(untrusted data): {payloads['packages.json'].get('generated_at')}")
    print(f"packages: {len(before)} -> {len(ids)}")
    print(f"新增: {added or '（无）'}")
    print(f"移除: {removed or '（无）'}")
    print(f"包含 {EXPECTED}: {EXPECTED in ids}")
    print(f"written: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
