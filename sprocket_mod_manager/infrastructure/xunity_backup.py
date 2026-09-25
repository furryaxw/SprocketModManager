from __future__ import annotations

import os
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from ..domain.errors import InstallError
from ..utilities.package_paths import validate_relative_path

# 每种「整体接管」的类型各自保留的最新归档份数。
REPLACED_BACKUP_LIMIT = 5
REPLACED_BACKUP_DIR_NAME = "replaced"


def replaced_backup_slug(file_type: str) -> str:
    """类型在文件系统里的名字：`xunity:translation` -> `xunity-translation`。

    Windows 路径段里不能出现 `:`。
    """
    return str(file_type).replace(":", "-")


def _replaced_backup_dir(backup_root: Path, file_type: str) -> Path:
    return backup_root / REPLACED_BACKUP_DIR_NAME / replaced_backup_slug(file_type)


def archive_replaced_directory(backup_root: Path, file_type: str, target: Path) -> Path | None:
    """把一个「整体接管」类型的供给目录打包成 zip，存到管理器在**游戏目录**里的备份区。

    `backup_root` 是 `<game>/SprocketModManager/backup`（见 `manager_paths.backups_dir`）：
    备份的是游戏目录里的文件，所以备份跟着游戏目录走。每种类型一个子目录，各自只留最新
    `REPLACED_BACKUP_LIMIT` 份。
    """
    if not target.exists():
        return None
    if not target.is_dir():
        raise InstallError(f"replaced target is not a directory: {target}")

    slug = replaced_backup_slug(file_type)
    backup_dir = _replaced_backup_dir(backup_root, file_type)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    archive_path = backup_dir / f"{slug}-{timestamp}.zip"
    temporary = backup_dir / f".{archive_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for current, directories, files in os.walk(target, topdown=True, followlinks=False):
                current_path = Path(current)
                directories.sort(key=str.casefold)
                files.sort(key=str.casefold)
                for name in directories:
                    _reject_linked_path(current_path / name)
                for name in files:
                    path = current_path / name
                    _reject_linked_path(path)
                    if not path.is_file():
                        raise InstallError(f"cannot back up unsupported path: {path}")
                    archive.write(path, path.relative_to(target).as_posix())
                if current_path != target and not directories and not files:
                    archive.writestr(current_path.relative_to(target).as_posix().rstrip("/") + "/", b"")
        os.replace(temporary, archive_path)
        archives = sorted(backup_dir.glob(f"{slug}-*.zip"), key=lambda item: item.name)
        for obsolete in archives[:-REPLACED_BACKUP_LIMIT]:
            obsolete.unlink()
        return archive_path
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        archive_path.unlink(missing_ok=True)
        if isinstance(exc, InstallError):
            raise
        raise InstallError(f"cannot archive {file_type} backup: {exc}") from exc


def latest_replaced_archive(backup_root: Path, file_type: str) -> Path | None:
    """这个类型最近一次整体接管前的归档；没有就是接管前目录还不存在。"""
    slug = replaced_backup_slug(file_type)
    backup_dir = _replaced_backup_dir(backup_root, file_type)
    archives = sorted(backup_dir.glob(f"{slug}-*.zip"), key=lambda item: item.name)
    return archives[-1] if archives else None


def restore_replaced_directory(backup_root: Path, file_type: str, target: Path) -> bool:
    """把最近一次归档解回供给目录；没有归档返回 False（接管前那里本来就没有东西）。"""
    archive_path = latest_replaced_archive(backup_root, file_type)
    if archive_path is None:
        return False
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            name = member.filename.replace("\\", "/")
            if name.endswith("/"):
                relative = validate_relative_path(name.rstrip("/"))
                (target / Path(*relative.parts)).mkdir(parents=True, exist_ok=True)
                continue
            relative = validate_relative_path(name)
            destination = target / Path(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    return True


def _reject_linked_path(path: Path) -> None:
    is_junction = getattr(path, "is_junction", lambda: False)
    if path.is_symlink() or is_junction():
        raise InstallError(f"cannot back up linked path: {path}")
