from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..domain.errors import InstallError
from ..utilities.package_paths import validate_relative_path


class FileTransaction:
    """Owns file-system backups, rollback ordering, and temporary cleanup.

    `state_dir` 是**游戏目录下的管理器状态目录**（`<game>/SprocketModManager`），
    备份落在 `<state_dir>/backup/<uuid>/files|directories/` —— 被覆盖的是游戏目录里的文件，
    备份就必须跟游戏目录在一起，不能塞进 AppData。
    """

    def __init__(self, state_dir: Path, *, prefix: str = "txn-"):
        root = Path(state_dir) / "backup"
        root.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
        self._files: dict[Path, Path | None] = {}
        self._directories: dict[Path, Path | None] = {}

    def backup_file(self, target: Path, key: str) -> None:
        """把 `target` 备份进事务目录。

        `key` 是这份备份在事务目录里的相对路径，由调用方给**记录形态**的路径
        （`Mods/X.dll`、`{Documents}/My Games/Sprocket/X.json`）—— 落点可能不在游戏目录里，
        而且两个根下的同名文件不能撞在一起。
        """
        if target in self._files:
            return
        if not target.exists():
            self._files[target] = None
            return
        backup = self._backup_path("files", key)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        self._files[target] = backup

    def backup_directory(self, target: Path, key: str) -> None:
        if target in self._directories:
            return
        if not target.exists():
            self._directories[target] = None
            return
        if not target.is_dir():
            raise InstallError(f"transaction target is not a directory: {target}")
        backup = self._backup_path("directories", key)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target, backup)
        self._directories[target] = backup

    def _backup_path(self, kind: str, key: str) -> Path:
        return self.path / kind / Path(*validate_relative_path(key).parts)

    def rollback(self) -> None:
        for target, backup in reversed(list(self._files.items())):
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
            except OSError:
                pass
        for target, backup in reversed(list(self._directories.items())):
            try:
                if target.exists():
                    shutil.rmtree(target)
                if backup is not None:
                    shutil.copytree(backup, target)
            except OSError:
                pass

    def close(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
