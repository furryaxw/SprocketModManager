"""实时判定「磁盘上的模组文件是否还是某个发布版本」：判定结果只活在内存里，不落盘。

设计要点：

* **判断是派生数据**，不该持久化：磁盘 hash 才是事实，发布版本 hash 是离线可得的参照
  （Registry 索引缓存里带完整 `releases[].assets[].digest` 历史）。
* 磁盘 hash 命中该 package **任意**发布版本的资产 digest → 正常（并顺手识别出命中的版本，
  用户自己手动升级/换版本不会被当成损坏）。
* 有发布数据、但一个都不匹配 → `corrupted`（本地自行构建的 DLL 也照此报红）。
* 该 package 没有任何发布数据（未发布 / 本地模组）→ `local`，不报损坏。
* 读不出 hash / 不是可管理文件 → `unreadable`，等同于损坏。
* 用户可以对具体文件「抑制」损坏提示（名单存游戏目录的 `SprocketModManager/suppression.json`，不进安装记录）：状态记为
  `suppressed`，界面要能看出它是"被抑制"而不是"正常"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..domain.models import RegistryPackage
from ..infrastructure.mod_toggle import actual_path_for, canonical_relative
from ..infrastructure.release_checksums import release_asset_sha256

STATUS_RELEASE = "release"
STATUS_CORRUPTED = "corrupted"
STATUS_LOCAL = "local"
STATUS_SUPPRESSED = "suppressed"
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


def classify(disk_hash: str | None, published: Mapping[str, str] | None, *, suppressed: bool) -> tuple[str, str]:
    """单个文件的判定：`(状态, 命中的发布版本)`。"""
    if suppressed:
        return STATUS_SUPPRESSED, ""
    if not disk_hash:
        return STATUS_UNREADABLE, ""
    if not published:
        return STATUS_LOCAL, ""
    version = published.get(disk_hash.casefold())
    if version:
        return STATUS_RELEASE, version
    return STATUS_CORRUPTED, ""


def package_status(statuses: Iterable[str]) -> str:
    """包级状态：只要有一个文件坏掉就报坏；全是抑制 → 抑制；否则看有没有对上发布版本。"""
    values = [status for status in statuses if status]
    if not values:
        return STATUS_LOCAL
    if any(status in BROKEN_STATUSES for status in values):
        return STATUS_CORRUPTED
    if all(status == STATUS_SUPPRESSED for status in values):
        return STATUS_SUPPRESSED
    if any(status == STATUS_RELEASE for status in values):
        return STATUS_RELEASE
    if any(status == STATUS_SUPPRESSED for status in values):
        # 一部分被抑制、一部分没有发布数据：算"本地"，但不能掩盖被抑制这件事
        return STATUS_SUPPRESSED
    return STATUS_LOCAL


def suppression_key(relative: str, package_id: str = "") -> str:
    """抑制键：**已归属**的文件用 `<package id>:<文件名>`，无归属才回退到规范相对路径。

    为什么按身份而不是按路径：路径只是"此刻这个文件在哪"。用身份做键之后，改名、在
    `Mods/` 与 `Plugins/` 之间挪动、以及被重新认领（adoption 会把记录挂到新路径）都不会丢抑制；
    而 `.dll` / `.dll.disable` 本来就归一（见 `canonical_relative`）。`:` 在 Windows 文件名里
    不可能出现，包 id 也不允许含 `:`，所以它是个安全的分隔符。
    """
    name = Path(canonical_relative(str(relative).replace("\\", "/"))).name
    package = str(package_id or "").strip()
    if package and ":" not in package:
        return f"{package}:{name}"
    return canonical_relative(relative)


def _is_package_key(entry: str) -> bool:
    head, separator, _ = entry.partition(":")
    return bool(separator) and "/" not in head and "\\" not in head


def suppression_keys(entries: Iterable[str] | None) -> set[str]:
    """把抑制条目归一成可比较的键（大小写不敏感；路径形式去掉 `.disable`）。"""
    keys: set[str] = set()
    for entry in entries or ():
        if not isinstance(entry, str):
            continue
        text = entry.strip()
        if not text:
            continue
        keys.add(text.replace("\\", "/").casefold() if _is_package_key(text) else canonical_relative(text).casefold())
    return keys


def suppression_key_of(relative: str, owners: Iterable[str]) -> str:
    """某个受管文件的**唯一**抑制键：有归属用第一个 owner 的身份键，无归属用规范路径。"""
    owner = next((str(item) for item in owners or () if str(item).strip()), "")
    return suppression_key(relative, owner)


def is_suppressed(relative: str, owners: Iterable[str], keys: set[str]) -> bool:
    """只认这个文件的规范键（身份键或路径键），不做跨形式匹配 —— 发布前不写兼容层。"""
    return suppression_key_of(relative, owners).casefold() in keys


def suppression_report(state: dict[str, Any], entries: Iterable[str] | None) -> dict[str, list[str]]:
    """把抑制条目分成「还有效」与「已失效」两份（失效的由调用方从名单里清掉）。

    有效 = 键与磁盘/记录对得上：身份键要求包记录还在；路径键要求那个文件存在**且没有归属**
    （有归属的文件只该用身份键，写在路径上的条目属于失效，直接清）。
    """
    packages = state.get("packages") if isinstance(state.get("packages"), dict) else {}
    files = state.get("files") if isinstance(state.get("files"), dict) else {}
    owned_paths = {
        canonical_relative(str(path))
        for path, entry in files.items()
        if isinstance(entry, dict) and entry.get("owners")
    }

    valid: list[str] = []
    stale: list[str] = []
    for entry in entries or ():
        if not isinstance(entry, str) or not entry.strip():
            continue
        text = entry.strip()
        if _is_package_key(text):
            package_id = text.partition(":")[0]
            (valid if package_id in packages else stale).append(text)
            continue
        path = canonical_relative(text)
        (valid if path in files and path not in owned_paths else stale).append(text)
    return {"valid": valid, "stale": stale}


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
        suppressed: Iterable[str],
        hash_provider: HashProvider,
        hashes: Mapping[str, str] | None = None,
) -> None:
    """把实时判定写进**内存里的**状态视图（调用方不落盘）。

    `state["files"]` 每个条目补上 `disk_sha256` / `integrity` / `matched_version`；
    `state["packages"]` 每个包补上 `integrity` / `corrupted`（布尔）；
    `state["suppression"]` 给出抑制条目的有效/失效清单，调用方据此清理游戏目录名单里的死条目。

    `hashes` 给"刚强制重算过"的调用方用：命中的路径直接用这份结果，不再走 `hash_provider`。
    """
    published_by_package = published_by_package or {}
    suppressed_set = suppression_keys(suppressed)
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
        suppressed_here = is_suppressed(key, owners, suppressed_set)
        status, version = classify(disk_hash, merged, suppressed=suppressed_here)
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
        package["suppressed"] = status == STATUS_SUPPRESSED

    state["suppression"] = suppression_report(state, suppressed)
