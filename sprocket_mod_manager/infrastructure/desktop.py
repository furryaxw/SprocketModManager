from __future__ import annotations

import os
import subprocess
from pathlib import Path


def open_directory(path: Path) -> None:
    directory = path.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    _start(directory)


def reveal_in_file_manager(path: Path) -> None:
    """在资源管理器里定位这个文件；文件已经不在磁盘上时打开它所在的目录。

    位置本身不存在就直接报错 —— 新建一个空目录再打开等于假装那里有东西。
    """
    target = path.expanduser().resolve()
    if target.exists():
        _start(target, select=True)
        return
    directory = target.parent
    if not directory.is_dir():
        raise OSError(f"location is not on disk: {target}")
    _start(directory)


def _start(path: Path, *, select: bool = False) -> None:
    if os.name != "nt":
        raise OSError("opening locations is only supported on Windows")
    if select:
        # `explorer /select,<路径>`：打开文件所在目录，并在这个目录里选中它。
        subprocess.Popen(["explorer", f"/select,{path}"])
        return
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise OSError("opening locations is only supported on Windows")
    startfile(str(path))
