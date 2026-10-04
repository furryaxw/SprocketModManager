from __future__ import annotations

import os
import subprocess
from pathlib import Path

_FILE_MANAGER_TIMEOUT_SECONDS = 5


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
    if select:
        _select(path)
        return
    _open(path)


def _select(path: Path) -> None:
    if os.name == "nt":
        # `explorer /select,<路径>`：打开文件所在目录，并在这个目录里选中它。
        subprocess.Popen(["explorer", f"/select,{path}"])
        return
    # freedesktop 的 FileManager1 才能选中文件；桌面没提供这个服务时就只打开目录。
    if _show_items(path):
        return
    _open(path.parent)


def _show_items(path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "dbus-send", "--session", "--dest=org.freedesktop.FileManager1", "--type=method_call",
                "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                f"array:string:{path.as_uri()}", "string:",
            ],
            capture_output=True,
            timeout=_FILE_MANAGER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _open(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))
        return
    try:
        subprocess.Popen(["xdg-open", str(path)])
    except FileNotFoundError as exc:
        raise OSError("xdg-open is required to open locations") from exc
