from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..domain.errors import InstallError
from .mod_toggle import canonical_relative

# 存储格式 v2：**由 package 主导**。文件挂在所属包下面：
#
#   {
#     "schema_version": 2,
#     "packages": {
#       "<id>": {"name": ..., "version": ..., "requested": ..., "dependencies": [...],
#                "files": [{"path": "Mods/X.dll", "sha256": ..., "disabled": ...}]}
#     },
#     "unowned": {"<path>": {"sha256": ..., "disabled": ...}}
#   }
#
# `corrupted` 不是持久化字段：它由磁盘 hash 与发布版本 hash 现场比出来（`application/integrity.py`）。
# 状态文件只记事实（路径 / sha256 / disabled），判断永远实时算，不会陈旧。
#
# DLL 元数据缓存在同目录的独立文件 `file-metadata.json`（见
# `file_metadata.py`）；这个文件只管"哪个包拥有哪些文件"。
#
# 视图：`load()` 返回的字典里同时有 `packages`（`files` 是路径字符串列表）和一张**派生**出来的
# `files` 表（path → {owners, sha256, ...}）；`save()` 再把视图还原成 package 主导的存储。
SCHEMA_VERSION = 2
_STORAGE_FILE_KEYS = ("sha256", "disabled")
EMPTY_STATE: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "packages": {}, "files": {}, "metadata": {}}


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InstallError("installed state is malformed")
    return [item for item in value if isinstance(item, str) and item.strip()]


def _file_entry(raw: object) -> dict[str, Any]:
    entry = raw if isinstance(raw, dict) else {}
    normalized = {key: entry.get(key) for key in _STORAGE_FILE_KEYS}
    normalized["sha256"] = str(normalized["sha256"] or "")
    normalized["disabled"] = bool(normalized["disabled"])
    return normalized


def _merge_entry(target: dict[str, Any], incoming: dict[str, Any], *, disabled: bool) -> None:
    """把同一逻辑文件的两条记录合起来（规范键与 `.disable` 键可能同时存在）。"""
    target["owners"] = sorted(set(target.get("owners", ())) | set(incoming.get("owners", ())))
    if not target.get("sha256") and incoming.get("sha256"):
        target["sha256"] = incoming["sha256"]
    target["disabled"] = bool(target.get("disabled")) or bool(incoming.get("disabled")) or disabled


def _to_view(raw: dict[str, Any]) -> dict[str, Any]:
    """把磁盘上的存储（v1 文件表 / v2 package 主导）统一成兼容视图。"""
    version = raw.get("schema_version")
    if version not in (1, SCHEMA_VERSION):
        raise InstallError("unsupported installed state schema")
    packages_raw = raw.get("packages")
    if not isinstance(packages_raw, dict):
        raise InstallError("installed state is malformed")
    files_raw = raw.get("files")
    unowned_raw = raw.get("unowned")
    if version == 1 and not isinstance(files_raw, dict):
        raise InstallError("installed state is malformed")
    if version == SCHEMA_VERSION and files_raw is not None and not isinstance(files_raw, dict):
        raise InstallError("installed state is malformed")

    files: dict[str, dict[str, Any]] = {}
    packages: dict[str, dict[str, Any]] = {}

    def store_file(relative: str, entry: dict[str, Any], *, owners: list[str] | None = None) -> str:
        """把文件记到**规范键**（去掉 `.disable`）上；`.disable` 只体现为 `disabled` 标志。"""
        canonical = canonical_relative(relative)
        existing = files.get(canonical)
        if existing is None:
            entry["owners"] = sorted(set(owners if owners is not None else entry.get("owners", ())))
            files[canonical] = entry
        else:
            if owners is not None:
                entry["owners"] = owners
            _merge_entry(existing, entry, disabled=canonical != relative)
        return canonical

    # v1：文件表是权威的（owners 列表给出归属）。
    if version == 1:
        for relative, entry in files_raw.items():
            if not isinstance(relative, str) or not relative.strip() or not isinstance(entry, dict):
                continue
            normalized = _file_entry(entry)
            normalized["owners"] = _string_list(entry.get("owners", []))
            store_file(relative, normalized)

    for package_id, package in packages_raw.items():
        if not isinstance(package_id, str) or not package_id.strip() or not isinstance(package, dict):
            raise InstallError("installed state is malformed")
        normalized_package = {key: value for key, value in package.items() if key != "files"}
        normalized_package["dependencies"] = _string_list(package.get("dependencies", []))
        listed = package.get("files", [])
        if listed is None:  # 旧版本可能写成 null：按空列表处理（与 dependencies 一致）
            listed = []
        if not isinstance(listed, list):
            raise InstallError("installed state is malformed")
        paths: list[str] = []
        for item in listed:
            if isinstance(item, dict):  # v2 存储：文件对象直接挂在包下面
                relative = item.get("path")
                if not isinstance(relative, str) or not relative.strip():
                    continue
                entry = _file_entry(item)
                owners = sorted(set(files.get(canonical_relative(relative), {}).get("owners", [])) | {package_id})
                canonical = store_file(relative, entry, owners=owners)
            elif isinstance(item, str) and item.strip():
                relative = item
                canonical = canonical_relative(relative)
                existing = files.setdefault(canonical, _file_entry({}))
                existing["owners"] = sorted(set(existing.get("owners", [])) | {package_id})
                if canonical != relative:
                    existing["disabled"] = True
            else:
                continue
            if canonical not in paths:
                paths.append(canonical)
        normalized_package["files"] = paths
        packages[package_id] = normalized_package

    if version == SCHEMA_VERSION and isinstance(unowned_raw, dict):
        for relative, entry in unowned_raw.items():
            if not isinstance(relative, str) or not relative.strip() or not isinstance(entry, dict):
                continue
            normalized = _file_entry(entry)
            normalized["owners"] = []
            store_file(relative, normalized)

    metadata = raw.get("metadata")
    return {
        "schema_version": SCHEMA_VERSION,
        "packages": packages,
        "files": files,
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


def _to_storage(view: dict[str, Any]) -> dict[str, Any]:
    """把兼容视图还原成 package 主导的存储（`save()` 落盘的就是这个形状）。"""
    raw_files = view.get("files") if isinstance(view.get("files"), dict) else {}
    # 落盘前也把键归一成规范路径：调用方（例如改名同步）可能自己搬过键，
    # 不归一就会把 `X.dll.disable` 又写回存储里 —— `.dll` 与 `.dll.disable` 是同一个逻辑文件。
    files: dict[str, Any] = {}
    for relative, entry in raw_files.items():
        if not isinstance(relative, str) or not relative.strip() or not isinstance(entry, dict):
            continue
        canonical = canonical_relative(relative)
        if canonical in files:
            _merge_entry(files[canonical], entry, disabled=canonical != relative)
            continue
        if canonical != relative:
            entry = dict(entry)
            entry["disabled"] = True
        files[canonical] = entry

    packages: dict[str, Any] = {}
    for package_id, package in (view.get("packages") or {}).items():
        if not isinstance(package_id, str) or not package_id.strip() or not isinstance(package, dict):
            raise InstallError("installed state is malformed")
        stored = {
            "name": package.get("name", ""),
            "repository": package.get("repository", ""),
            "version": package.get("version", ""),
            "requested": bool(package.get("requested")),
            "install_mode": package.get("install_mode", "standard"),
            "dependencies": _string_list(package.get("dependencies", [])),
            # 安装时**新建的目录**（卸载按记录自深到浅删空目录）。白名单式存储必须显式列出，
            # 否则这个字段会在落盘时被静默丢掉。
            "directories": _string_list(package.get("directories", [])),
        }
        for extra in ("assets",):
            if extra in package:
                stored[extra] = package[extra]
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        listed = package.get("files", [])
        if isinstance(listed, list):
            for item in listed:
                if isinstance(item, dict):
                    relative = item.get("path")
                elif isinstance(item, str):
                    relative = item
                else:
                    continue
                if not isinstance(relative, str) or not relative.strip():
                    continue
                canonical = canonical_relative(relative)
                if canonical in seen:
                    continue
                seen.add(canonical)
                entry = files.get(canonical) if isinstance(files.get(canonical), dict) else item
                entries.append({"path": canonical, **_file_entry(entry)})
        stored["files"] = entries
        packages[package_id] = stored

    claimed = {
        entry["path"]
        for package in packages.values()
        for entry in package["files"]
    }
    unowned: dict[str, Any] = {}
    for relative, entry in files.items():
        if not isinstance(relative, str) or not relative.strip() or relative in claimed:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("owners"):
            # 自己有 owners 列表但没有任何包认领它：属于脏数据，按无归属收起来。
            pass
        unowned[relative] = _file_entry(entry)

    return {
        "schema_version": SCHEMA_VERSION,
        "packages": packages,
        "unowned": unowned,
    }


class StateStore:
    """`<game>/SprocketModManager/installed.json`（package 主导）。"""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return deepcopy(EMPTY_STATE)
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InstallError(f"cannot read installed state: {exc}") from exc
        if not isinstance(state, dict):
            raise InstallError("installed state is malformed")
        return _to_view(state)

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        storage = _to_storage(deepcopy(state))
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        data = (json.dumps(storage, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary.write_bytes(data)
        os.replace(temporary, self.path)
