"""实时判定「磁盘上的模组文件是否还是某个发布版本」：判定结果只活在内存里，不落盘。

设计要点：

* **判断是派生数据**，不该持久化：磁盘 hash 才是事实，发布版本 hash 是离线可得的参照
  （Registry 索引缓存里带完整 `releases[].assets[].digest` 历史）。
* 磁盘 hash 命中该 package **任意**发布版本的资产 digest → 正常（并顺手识别出命中的版本，
  用户自己手动升级/换版本不会被当成损坏）。
* 有发布数据、但一个都不匹配 → `corrupted`（本地自行构建的 DLL 也照此报红）。
* 该 package 没有任何发布数据（未发布 / 本地模组）→ `local`，不报损坏。
* 读不出 hash / 不是可管理文件 → `unreadable`，等同于损坏。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..domain.models import RegistryPackage
from ..infrastructure.mod_toggle import actual_path_for
from ..infrastructure.release_checksums import release_asset_sha256

STATUS_RELEASE = "release"
STATUS_CORRUPTED = "corrupted"
STATUS_LOCAL = "local"
STATUS_UNREADABLE = "unreadable"

BROKEN_STATUSES = frozenset({STATUS_CORRUPTED, STATUS_UNREADABLE})

HashProvider = Callable[[Path], "str | None"]


def published_hashes(packages: Iterable[RegistryPackage] | None) -> dict[str, dict[str, dict[str, str]]]:
    """`package id -> {资产文件名(小写): {sha256: 版本}}`，来自索引里的**全部**发布版本。

    按文件名分组是必须的：有的包发布的是**压缩包**（例如 UnityExplorer 的 `.zip`），解压出来的
    DLL 永远不可能等于压缩包自己的 digest —— 那种文件我们**没有可比的发布数据**，
    应该落在 `local`（不判断），而不是被误判成"损坏"。
    """
    table: dict[str, dict[str, dict[str, str]]] = {}
    for package in packages or ():
        releases = getattr(package, "releases", None)
        if not releases:
            continue
        per_package: dict[str, dict[str, str]] = {}
        for release in releases:
            version = str(getattr(release, "version", "") or "")
            for asset in getattr(release, "assets", ()) or ():
                digest = release_asset_sha256(asset)
                name = str(getattr(asset, "name", "") or "").casefold()
                if not digest or not name:
                    continue
                per_package.setdefault(name, {}).setdefault(digest.casefold(), version)
        if per_package:
            table[str(package.id)] = per_package
    return table


def classify(disk_hash: str | None, published: Mapping[str, str] | None) -> tuple[str, str]:
    """单个文件的判定：`(状态, 命中的发布版本)`。"""
    if not disk_hash:
        return STATUS_UNREADABLE, ""
    if not published:
        return STATUS_LOCAL, ""
    version = published.get(disk_hash.casefold())
    if version:
        return STATUS_RELEASE, version
    return STATUS_CORRUPTED, ""


def package_status(statuses: Iterable[str]) -> str:
    """包级状态：只要有一个文件坏掉就报坏，否则看有没有对上发布版本。"""
    values = [status for status in statuses if status]
    if not values:
        return STATUS_LOCAL
    if any(status in BROKEN_STATUSES for status in values):
        return STATUS_CORRUPTED
    if any(status == STATUS_RELEASE for status in values):
        return STATUS_RELEASE
    return STATUS_LOCAL


def _disk_target(root: Path, relative: str) -> Path:
    """受管文件在磁盘上的真实路径（变体判断交给 `mod_toggle.actual_path_for`）。

    禁用是改名（`X.dll` → `X.dll.disable`），而状态里只记规范路径 —— 直接拿规范路径去算 hash 会
    对禁用中的文件返回"读不到"，把整个包误判成损坏。找不到时把规范路径交回去，由上层判为读不到。
    """
    target = root / str(relative).replace("/", "\\")
    return actual_path_for(target) or target


def annotate(
        state: dict[str, Any],
        *,
        game_dir: Path,
        published_by_package: Mapping[str, Mapping[str, Mapping[str, str]]] | None,
        hash_provider: HashProvider,
        hashes: Mapping[str, str] | None = None,
) -> None:
    """把实时判定写进**内存里的**状态视图（调用方不落盘）。

    `state["files"]` 每个条目补上 `disk_sha256` / `integrity` / `matched_version`；
    `state["packages"]` 每个包补上 `integrity` / `corrupted`（布尔）。

    `hashes` 给"刚强制重算过"的调用方用：命中的路径直接用这份结果，不再走 `hash_provider`。
    """
    published_by_package = published_by_package or {}
    precomputed = {str(key): str(value) for key, value in (hashes or {}).items()}

    root = Path(game_dir).expanduser()
    files = state.get("files") if isinstance(state.get("files"), dict) else {}
    for relative, entry in files.items():
        if not isinstance(entry, dict):
            continue
        owners = entry.get("owners") or ()
        file_name = Path(str(relative).replace("\\", "/")).name.casefold()
        merged: dict[str, str] = {}
        for package_id in owners:
            merged.update((published_by_package.get(str(package_id), {}) or {}).get(file_name, {}))
        key = str(relative)
        disk_hash = precomputed.get(key) if key in precomputed else hash_provider(_disk_target(root, key))
        status, version = classify(disk_hash, merged)
        entry["disk_sha256"] = disk_hash or ""
        entry["integrity"] = status
        entry["matched_version"] = version
        entry["recorded_sha256"] = str(entry.get("sha256") or "")

    packages = state.get("packages") if isinstance(state.get("packages"), dict) else {}
    for package in packages.values():
        if not isinstance(package, dict):
            continue
        paths = package.get("files") or ()
        statuses = [
            files[path].get("integrity", "")
            for path in paths
            if isinstance(path, str) and isinstance(files.get(path), dict)
        ]
        status = package_status(statuses)
        package["integrity"] = status
        package["corrupted"] = status == STATUS_CORRUPTED
