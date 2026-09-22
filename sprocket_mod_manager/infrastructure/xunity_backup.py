from __future__ import annotations

import os
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from ..domain.errors import InstallError

XUNITY_TRANSLATION_BACKUP_LIMIT = 5


def is_xunity_translation_path(relative: str) -> bool:
    parts = relative.replace("\\", "/").split("/")
    return bool(parts) and parts[0].casefold() == "autotranslator"


def archive_xunity_translation_backup(backup_root: Path, target: Path) -> Path | None:
    """把 `AutoTranslator` 目录打包成 zip，存到管理器在**游戏目录**里的备份区。

    `backup_root` 是 `<game>/SprocketModManager/backup`（见 `manager_paths.backups_dir`）：
    备份的是游戏目录里的文件，所以备份跟着游戏目录走。
    """
    if not target.exists():
        return None
    if not target.is_dir():
        raise InstallError(f"translation target is not a directory: {target}")

    backup_dir = backup_root / "AutoTranslator"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    archive_path = backup_dir / f"AutoTranslator-{timestamp}.zip"
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
        archives = sorted(backup_dir.glob("AutoTranslator-*.zip"), key=lambda item: item.name)
        for obsolete in archives[:-XUNITY_TRANSLATION_BACKUP_LIMIT]:
            obsolete.unlink()
        return archive_path
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        archive_path.unlink(missing_ok=True)
        if isinstance(exc, InstallError):
            raise
        raise InstallError(f"cannot archive AutoTranslator backup: {exc}") from exc


def _reject_linked_path(path: Path) -> None:
    is_junction = getattr(path, "is_junction", lambda: False)
    if path.is_symlink() or is_junction():
        raise InstallError(f"cannot back up linked path: {path}")
