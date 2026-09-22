"""本地已安装模组的清单：把磁盘上的 DLL（含 ``*.dll.disable``）与安装记录、Registry 对齐。

这是管理器「识别能力」的核心：每个条目都带静态元数据
（显示名/版本/作者/声明 ID）、Registry 匹配结果、安装记录归属和禁用状态。
读取只走 :func:`read_dll_metadata`（PE/.NET 静态解析，绝不加载程序集）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..domain.models import RegistryPackage
from ..infrastructure.dll_metadata import clear_metadata_cache, flush_metadata_cache, read_cached_metadata
from ..infrastructure.mod_toggle import (
    canonical_relative,
    direct_files,
    is_disabled_path,
    is_loadable_path,
)
from ..utilities.checksums import sha256_file
from .adoption import match_package_by_metadata

DEFAULT_ROOTS = ("Mods", "Plugins", "UserLibs")
_ROOT_ORDER = {name: index for index, name in enumerate(DEFAULT_ROOTS)}


def _sort_key(mod: LocalMod) -> tuple[int, bool, str, str]:
    """清单顺序：按根目录分组（`Mods` → `Plugins` → `UserLibs`），组内禁用项最后、再按显示名。

    分组看**路径根目录**而不是 DLL 自报的 `kind`：MelonLoader 按所在目录决定加载类别，
    一个放在 `UserLibs` 里却继承 `MelonMod` 的程序集也不该跑到模组前面。
    """
    root = mod.path.split("/", 1)[0]
    return (
        _ROOT_ORDER.get(root, len(DEFAULT_ROOTS)),
        mod.disabled,
        mod.display_name.casefold(),
        mod.path.casefold(),
    )


@dataclass(frozen=True)
class LocalMod:
    path: str
    name: str
    display_name: str
    version: str
    authors: tuple[str, ...]
    kind: str
    disabled: bool
    declared_id: str
    registry_id: str
    registry_match: str
    required_dependencies: tuple[str, ...] = ()
    incompatible_assemblies: tuple[str, ...] = ()
    missing_dependencies: tuple[str, ...] = ()
    registry_display_name: dict[str, str] = field(default_factory=dict)
    registry_description: dict[str, str] = field(default_factory=dict)
    installed_package_id: str = ""
    assembly_name: str = ""
    sha256: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "display_name": self.display_name,
            "version": self.version,
            "authors": list(self.authors),
            "kind": self.kind,
            "disabled": self.disabled,
            "declared_id": self.declared_id,
            "registry_id": self.registry_id,
            "registry_match": self.registry_match,
            "required_dependencies": list(self.required_dependencies),
            "incompatible_assemblies": list(self.incompatible_assemblies),
            "missing_dependencies": list(self.missing_dependencies),
            "registry_display_name": dict(self.registry_display_name),
            "registry_description": dict(self.registry_description),
            "installed_package_id": self.installed_package_id,
            "assembly_name": self.assembly_name,
            "sha256": self.sha256,
            "error": self.error,
        }


def _relative_posix(path: Path, game_path: Path) -> str:
    try:
        return path.relative_to(game_path).as_posix()
    except ValueError:
        return path.as_posix()


def scan_local_mods(
        game_path: Path,
        managed_paths: Mapping[str, str],
        packages: Sequence[RegistryPackage] = (),
        roots: Sequence[str] = DEFAULT_ROOTS,
        *,
        compute_hashes: bool = False,
) -> list[LocalMod]:
    """扫描 ``Mods`` / ``Plugins`` / ``UserLibs``，返回排序后的本地模组清单。

    每个根目录只扫 **1 层**（直接子文件），子目录里的 DLL 不算模组。
    顺序见 :func:`_sort_key`：`Mods`、`Plugins`、`UserLibs` 依次成段。

    ``managed_paths`` 是「相对路径（小写、正斜杠）→ package id」的映射，来自安装记录。
    任何单个文件的解析失败都降级成 ``error`` 字段，不影响其他条目。

    ``compute_hashes`` 默认为 False：列表刷新只需要身份与状态，算 SHA-256 会让大程序集拖慢整页；
    需要哈希的调用方（CLI、安装期校验）显式传 True。
    """
    managed = {key.casefold(): value for key, value in managed_paths.items()}
    found: list[LocalMod] = []
    for root_name in roots:
        root = game_path / root_name
        if not root.is_dir():
            continue
        for path in _iter_candidates(root):
            relative = _relative_posix(path, game_path)
            disabled = is_disabled_path(path)
            display_name = path.name
            version = ""
            authors: tuple[str, ...] = ()
            kind = root_name
            declared = ""
            registry_id = ""
            registry_match = ""
            registry_display_name: dict[str, str] = {}
            registry_description: dict[str, str] = {}
            assembly_name = ""
            digest = ""
            error = ""
            required_dependencies: tuple[str, ...] = ()
            incompatible_assemblies: tuple[str, ...] = ()

            try:
                metadata = read_cached_metadata(path)
            except Exception as exc:  # noqa: BLE001 - 单个文件失败不能中断整次扫描
                error = str(exc)
                metadata = None

            if metadata is not None:
                kind = metadata.melon_kind or root_name
                assembly_name = str(metadata.assembly_name or "")
                required_dependencies = tuple(metadata.required_dependencies)
                incompatible_assemblies = tuple(metadata.incompatible_assemblies)
                display_name = str(metadata.sprocket.get("display_name", "") or "") or str(metadata.melon_name or "") or assembly_name or path.name
                version = str(metadata.melon_version or metadata.assembly_version or metadata.file_version or "")
                declared = str(metadata.sprocket.get("id", "") or "")
                author_field = str(metadata.sprocket.get("authors", "") or "")
                if author_field:
                    authors = tuple(part.strip() for part in author_field.split(",") if part.strip())
                elif metadata.melon_author:
                    authors = (str(metadata.melon_author),)

                match = match_package_by_metadata(metadata, packages)
                if match is not None:
                    registry_id = match.package_id
                    registry_match = match.reason
                    for package in packages:
                        if package.id == match.package_id:
                            registry_display_name = dict(package.display_name)
                            registry_description = dict(package.description)
                            break

            if compute_hashes:
                try:
                    digest = sha256_file(path)
                except OSError as exc:
                    error = error or str(exc)

            found.append(
                LocalMod(
                    path=relative,
                    name=path.name,
                    display_name=display_name,
                    version=version,
                    authors=authors,
                    kind=kind,
                    disabled=disabled,
                    declared_id=declared,
                    registry_id=registry_id,
                    registry_match=registry_match,
                    required_dependencies=required_dependencies,
                    incompatible_assemblies=incompatible_assemblies,
                    registry_display_name=registry_display_name,
                    registry_description=registry_description,
                    installed_package_id=managed.get(canonical_relative(relative).casefold(), ""),
                    assembly_name=assembly_name,
                    sha256=digest,
                    error=error,
                )
            )

    found.sort(key=_sort_key)
    resolved = apply_dependency_graph(found)
    # 扫描是**唯一会触发新解析**的地方，所以缓存必须在这里落盘。
    # 不在这里落盘，命令行 `modman local-mods` 每次都从零解析：同样 13 个真实 DLL，
    # 冷启动约 2.8 s，落盘缓存命中后下一次只要 ~0.44 s。
    flush_metadata_cache()
    return resolved


def apply_dependency_graph(mods: list[LocalMod]) -> list[LocalMod]:
    """用 DLL 元数据里的 `MelonAdditionalDependencies` 构建本地依赖关系。

    依赖的"是否满足"以**本机存在同名程序集**为准：已加载的 melon 管不了 `UserLibs` 里的库，
    而模组依赖库是合法的，所以这里用 `ModAssemblyIndex` 的同一套判据——
    程序集名 + 文件名主干（`*.dll` / `*.dll.disable` 都算）。
    """
    if not mods:
        return mods

    available: set[str] = set()
    for mod in mods:
        if mod.assembly_name:
            available.add(mod.assembly_name.casefold())
        stem = Path(mod.name).name
        if stem.casefold().endswith(".dll.disable"):
            stem = stem[: -len(".dll.disable")]
        elif stem.casefold().endswith(".dll"):
            stem = stem[: -len(".dll")]
        if stem:
            available.add(stem.casefold())

    resolved: list[LocalMod] = []
    for mod in mods:
        own = {mod.assembly_name.casefold()} if mod.assembly_name else set()
        missing: list[str] = []
        for dependency in mod.required_dependencies:
            name = dependency.strip()
            if not name or name.casefold() in own or name.casefold() in available:
                continue
            if name not in missing:
                missing.append(name)
        resolved.append(
            replace(mod, missing_dependencies=tuple(missing)) if missing or mod.missing_dependencies else mod
        )
    return resolved


def _iter_candidates(root: Path) -> list[Path]:
    candidates = [
        path
        for path in direct_files(root)
        if is_loadable_path(path) or is_disabled_path(path)
    ]
    return sorted(candidates, key=lambda item: str(item).casefold())


def summarize(mods: Sequence[LocalMod]) -> dict[str, int]:
    return {
        "total": len(mods),
        "disabled": sum(1 for mod in mods if mod.disabled),
        "registry_matched": sum(1 for mod in mods if mod.registry_id),
        "unmanaged": sum(1 for mod in mods if not mod.installed_package_id),
        "unreadable": sum(1 for mod in mods if mod.error),
        # 依赖关系来自 DLL 元数据（MelonAdditionalDependencies），不依赖 installed.json。
        "missing_dependencies": sum(1 for mod in mods if mod.missing_dependencies),
    }
