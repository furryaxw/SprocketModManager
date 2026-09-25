"""加载器安装位置的归档区：卸载时整棵搬进备份区，重装同一个加载器时再搬回来。

卸载加载器不能只交还它自己的树：它供给的目录（`Mods`、`Plugins`、`UserLibs`、桥接的 `MLLoader/Mods` …）
里躺着别的包的文件，那些类型在加载器走后没有任何供给者。搬进归档而不是删掉，重新装**同一个**加载器时
再搬回来 —— 模组跟着加载器来回走，中间既不会标着「已安装」却动不了，也不会被误删。

归档在 `<game>/SprocketModManager/backup/loaders/<slug>/`：`payload/` 下按游戏目录内的相对路径原样存放
被搬走的东西，`manifest.json` 记这次搬走了哪些位置、哪些包的文件跟着进了归档。还原以盘上的那份为准：
目标位置已经被别的东西占住的不覆盖、不删除，留在归档里下次再说。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from ..domain.errors import InstallError
from ..utilities.checksums import sha256_file
from ..utilities.package_paths import validate_relative_path

LOGGER = logging.getLogger(__name__)

LOADER_ARCHIVE_DIR_NAME = "loaders"
MANIFEST_FILE_NAME = "manifest.json"
PAYLOAD_DIR_NAME = "payload"
MANIFEST_SCHEMA_VERSION = 1


def loader_archive_slug(loader_id: str) -> str:
    """加载器 id 在文件系统里的名字（包 id 里的字符不都能当目录名）。"""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(loader_id))


def loader_archive_dir(backup_root: Path, loader_id: str) -> Path:
    return Path(backup_root) / LOADER_ARCHIVE_DIR_NAME / loader_archive_slug(loader_id)


def payload_dir(archive_dir: Path) -> Path:
    return Path(archive_dir) / PAYLOAD_DIR_NAME


def read_manifest(archive_dir: Path) -> dict[str, Any] | None:
    """归档清单；没有或读不出来都返回 None（没有清单就当这个归档不存在）。"""
    path = Path(archive_dir) / MANIFEST_FILE_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        LOGGER.warning("could not read loader archive manifest %s: %s", path, exc)
        return None
    return payload if isinstance(payload, dict) else None


def write_manifest(archive_dir: Path, manifest: dict[str, Any]) -> None:
    root = Path(archive_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST_FILE_NAME
    temporary = root / f".{MANIFEST_FILE_NAME}.{uuid.uuid4().hex}.tmp"
    body = dict(manifest)
    body["schema_version"] = MANIFEST_SCHEMA_VERSION
    try:
        temporary.write_text(
            json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise InstallError(f"cannot write loader archive manifest: {exc}") from exc


def discard_archive(archive_dir: Path) -> None:
    shutil.rmtree(Path(archive_dir), ignore_errors=True)


def archive_location(archive_dir: Path, game_dir: Path, relative: str) -> bool:
    """把游戏目录里的一个位置整棵搬进归档；那里本来就没有东西时返回 False。

    归档里已经有一份同名东西（上一次卸载没能还原干净）时按「先到的那份为准」合并，
    两份内容不同只告警，不覆盖。
    """
    source = _game_path(game_dir, relative)
    if not source.exists():
        return False
    destination = payload_dir(archive_dir) / Path(*validate_relative_path(relative).parts)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(destination))
        except OSError as exc:
            raise InstallError(f"cannot move {relative} into the loader backup: {exc}") from exc
        return True
    warnings: list[str] = []
    _merge(source, destination, relative, [], warnings)
    for line in warnings:
        LOGGER.warning("loader archive merge: %s", line)
    return not source.exists()


def restore_archive(archive_dir: Path, game_dir: Path, warnings: list[str]) -> list[str]:
    """把归档里的东西搬回游戏目录，返回**没能**搬回的相对路径（它们继续留在归档里）。

    目标位置已经有内容时以盘上的那份为准：内容一样就丢掉归档里的副本，不一样就原样留在归档里，
    并往 `warnings` 里写一条能看懂的原因。
    """
    payload = payload_dir(archive_dir)
    if not payload.is_dir():
        return []
    blocked: list[str] = []
    for child in sorted(payload.iterdir(), key=lambda item: item.name.casefold()):
        try:
            destination = _game_path(game_dir, child.name)
        except InstallError:
            blocked.append(child.name)
            warnings.append(f"kept {child.name} in the loader backup: unsafe path")
            continue
        _merge(child, destination, child.name, blocked, warnings)
    _prune_empty_directories(payload)
    return blocked


def _game_path(game_dir: Path, relative: str) -> Path:
    relative_path = validate_relative_path(relative)
    root = Path(game_dir).resolve()
    full = (root / Path(*relative_path.parts)).resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise InstallError(f"path escapes the game directory: {relative}") from exc
    return full


def _merge(source: Path, destination: Path, relative: str, blocked: list[str], warnings: list[str]) -> None:
    """把 `source` 搬进 `destination`：目标已有的内容不覆盖，冲突留在原地。"""
    if source.is_dir():
        if destination.exists() and not destination.is_dir():
            _block(relative, blocked, warnings, "a file is in the way")
            return
        destination.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
            _merge(child, destination / child.name, _join(relative, child.name), blocked, warnings)
        _remove_if_empty(source)
        return
    if not source.is_file():
        return
    if destination.exists() and destination.is_dir():
        _block(relative, blocked, warnings, "a directory is in the way")
        return
    if destination.is_file():
        if _same_content(source, destination):
            _unlink(source)
            return
        _block(relative, blocked, warnings, "a different file is already there")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(source), str(destination))
    except OSError as exc:
        _block(relative, blocked, warnings, str(exc))


def _block(relative: str, blocked: list[str], warnings: list[str], reason: str) -> None:
    blocked.append(relative)
    warnings.append(f"kept existing {relative}: {reason}")


def _join(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


def _same_content(left: Path, right: Path) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        return sha256_file(left) == sha256_file(right)
    except OSError:
        return False


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError as exc:
        LOGGER.warning("could not drop a duplicate loader backup file %s: %s", path, exc)


def _remove_if_empty(path: Path) -> None:
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


def _prune_empty_directories(root: Path) -> None:
    for current, _directories, _files in os.walk(root, topdown=False):
        path = Path(current)
        if path != root:
            _remove_if_empty(path)
