"""诊断：状态文件里的 DLL 元数据缓存，和磁盘上的真实 DLL 是否一致（为什么依赖没刷新？）。

对 `installed.json` 的 `metadata` 段逐条比对：
  1. 缓存里的 size/mtime 与文件当前值是否相同（缓存键就是这两个 + 路径，**不含 hash**）
  2. 用缓存反序列化出来的 required_dependencies / incompatible_assemblies
     与**现在重新解析**同一份 DLL 得到的结果是否一致
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sprocket_mod_manager.application.local_mods import scan_local_mods  # noqa: E402
from sprocket_mod_manager.infrastructure import dll_metadata as md  # noqa: E402

GAME = Path(r"G:\Sprocket")
STATE = GAME / "SprocketModManager" / "installed.json"


def main() -> int:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    entries = state.get("metadata") or {}
    print(f"metadata 条目: {len(entries)}（状态文件 v{state.get('schema_version')}）")
    print()
    mismatched = 0
    for path_key, entry in sorted(entries.items()):
        path = Path(path_key)
        if not path.is_file():
            print(f"[缺文件] {path_key}")
            mismatched += 1
            continue
        stat = path.stat()
        size_ok = entry.get("size") == stat.st_size
        mtime_ok = entry.get("mtime") == stat.st_mtime_ns
        cached = md._deserialize(str(path), entry.get("data") or {})
        fresh = md.read_dll_metadata(path)
        same_deps = tuple(cached.required_dependencies) == tuple(fresh.required_dependencies)
        same_conf = tuple(cached.incompatible_assemblies) == tuple(fresh.incompatible_assemblies)
        flag = "OK" if (size_ok and mtime_ok and same_deps and same_conf) else "**不一致**"
        if flag != "OK":
            mismatched += 1
        print(f"{flag} {path.name}: size_ok={size_ok} mtime_ok={mtime_ok} deps_ok={same_deps} conflicts_ok={same_conf}")
        if not same_deps:
            print(f"      缓存 required_dependencies = {tuple(cached.required_dependencies)}")
            print(f"      新解析 required_dependencies = {tuple(fresh.required_dependencies)}")

    print()
    print(f"不一致条目: {mismatched}/{len(entries)}")
    print()
    mods = scan_local_mods(GAME, {}, ())
    print("磁盘扫描看到的依赖（只用 DLL 元数据，不联网）：")
    for mod in mods:
        if mod.required_dependencies or mod.incompatible_assemblies:
            print(f"  {mod.path}: requires={list(mod.required_dependencies)} conflicts={list(mod.incompatible_assemblies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
