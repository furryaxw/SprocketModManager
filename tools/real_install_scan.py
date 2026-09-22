"""只读：用**真实安装目录 + 仓库里的真实索引**跑一遍识别，打印 GUI 会显示的那张表。

刻意**不**构造 `ModManagerService`：那会碰到状态文件（reconcile 可能回写）。
这里只调用纯函数 `scan_local_mods()`（1 层目录扫描 + 静态 PE 解析，只读，不写任何东西）+
本地索引加载器（`source` 是本地路径 ⇒ 不联网），用来验证：
「磁盘扫描 + DLL 元数据 + Registry 匹配 + 缓存 Registry 的多语言名 + 缺依赖」在真机上成立。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(r"G:\Sprocket\sprocket-mod-system")
GAME = Path(r"G:\Sprocket")
INDEX = REPO / "index.json"
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sprocket_mod_manager.application.local_mods import scan_local_mods, summarize  # noqa: E402
from sprocket_mod_manager.application.adoption import (  # noqa: E402
    declared_id,
    normalize_repository,
    repository_of,
)
from sprocket_mod_manager.infrastructure.dll_metadata import read_cached_metadata  # noqa: E402
from sprocket_mod_manager.infrastructure.registry_source import RegistrySourceLoader  # noqa: E402


def why_not_matched(mod, metadata, registry_ids: set[str], registry_repos: set[str]) -> str:
    """给出"为什么显示为仅本地"的确切原因（按匹配顺序逐条排除）。"""
    declared = declared_id(metadata)
    repository = repository_of(metadata)
    if declared and declared.casefold() not in registry_ids:
        return f"声明了 Sprocket.Mod.Id={declared}，但 Registry 里没有这个 id"
    if repository and normalize_repository(repository) not in registry_repos:
        return f"声明了 Repository={repository}，但 Registry 里没有同一个仓库的包"
    if not declared and not repository:
        return "没有声明任何身份（Sprocket.Mod.Id / Repository）——设计上只显示为仅本地"
    return "有身份但未命中（需要人工检查）"


def resolve_index() -> Path:
    """挑一份**最新的** Registry：优先 AppData 里缓存的线上索引，其次仓库里的本地副本。

    优先取 AppData 里缓存的线上索引，其次仓库里的本地副本：本地 `index.json` 可能已过期
    （少收录了某些包，例如 SprocketModAPI），所以这里显式检查缓存，并打印实际用了哪份。
    """
    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "SprocketModManager" / "cache" / "http"
    best: tuple[float, Path] | None = None
    for sidecar in cache.glob("*.json"):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if "sprocketmods.furryaxw.top" not in str(meta.get("url") or ""):
            continue
        body = sidecar.with_suffix(".body")
        if not body.is_file():
            continue
        fetched = float(meta.get("fetched_at") or 0)
        if best is None or fetched > best[0]:
            best = (fetched, body)
    if best is not None:
        print(f"index 来源: AppData 缓存的线上索引（fetched_at={best[0]:.0f}）")
        return best[1]
    print("index 来源: 仓库本地副本（注意：可能已过期）")
    return INDEX


def main() -> int:
    index_path = resolve_index()
    registry = RegistrySourceLoader(None).load(index_path)  # 本地文件 → 不联网
    mods = scan_local_mods(GAME, {}, registry.packages)
    summary = summarize(mods)
    registry_ids = {package.id.casefold() for package in registry.packages}
    registry_repos = {normalize_repository(package.repository) for package in registry.packages}

    print(f"game: {GAME}")
    print(f"registry packages: {len(registry.packages)}")
    print()
    print(f"{'path':<46} {'version':<10} {'kind':<9} {'state':<9} {'match':<19} name(zh/en)")
    print("-" * 150)
    for mod in mods:
        localized = mod.registry_display_name.get("zh") or mod.registry_display_name.get("en") or ""
        name = localized or mod.display_name or mod.name
        state = "disabled" if mod.disabled else "loaded"
        missing = f"  MISSING={','.join(mod.missing_dependencies)}" if mod.missing_dependencies else ""
        incompatible = f"  CONFLICT={','.join(mod.incompatible_assemblies)}" if mod.incompatible_assemblies else ""
        print(f"{mod.path:<46} {mod.version or '-':<10} {mod.kind:<9} {state:<9} "
              f"{mod.registry_match or '-':<19} {name}{missing}{incompatible}")
        if not mod.registry_id:
            metadata = read_cached_metadata(GAME / mod.path)
            print(f"{'':<46} -> {why_not_matched(mod, metadata, registry_ids, registry_repos)}")
            print(f"{'':<46}    declared_id={declared_id(metadata) or '-'}  repository={repository_of(metadata) or '-'}")
        if mod.error:
            print(f"{'':<46} -> error: {mod.error}")

    print()
    print("summary:", {key: summary[key] for key in sorted(summary)})
    matched = sum(1 for mod in mods if mod.registry_id)
    print(f"识别命中: {matched}/{len(mods)}；i18n 名称来自 Registry 缓存，未命中则回落 DLL 自带英文名")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
