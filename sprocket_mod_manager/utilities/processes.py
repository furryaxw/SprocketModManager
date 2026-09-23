from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

SPROCKET_EXECUTABLE = "Sprocket.exe"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TASKKILL_TIMEOUT_SECONDS = 10


def running_executables(image_name: str) -> dict[int, Path]:
    """本机正在跑的 `image_name` 进程：PID → 可执行文件完整路径。

    路径读不到的进程（权限不足等）不列出来 —— 判定「某个目录里的程序在不在跑」时，
    一个位置未知的同名进程不能算数。
    """
    if os.name != "nt":
        return {}
    return _windows_executables(image_name)


def sprocket_processes(game_dir: Path | str | None) -> list[tuple[int, Path]]:
    """`game_dir` 里那个 Sprocket.exe 的进程（PID、路径）；没在跑就是空表。

    按完整路径匹配：同名但装在别的目录里的进程不算这个游戏在跑。
    """
    if not game_dir:
        return []
    target = _normalized(Path(game_dir).expanduser() / SPROCKET_EXECUTABLE)
    return [
        (pid, path)
        for pid, path in running_executables(SPROCKET_EXECUTABLE).items()
        if _normalized(path) == target
    ]


def sprocket_is_running(game_dir: Path | str | None) -> bool:
    """`game_dir` 里的 Sprocket.exe 是否正在运行。"""
    return bool(sprocket_processes(game_dir))


def terminate_sprocket(game_dir: Path | str | None) -> list[int]:
    """结束 `game_dir` 里的 Sprocket.exe，返回被结束的 PID。

    只结束路径匹配上的进程：游戏目录换了之后，别处那个同名进程不该被误伤。
    """
    pids = [pid for pid, _path in sprocket_processes(game_dir)]
    terminated: list[int] = []
    for pid in pids:
        if _terminate(pid):
            terminated.append(pid)
    return terminated


def _normalized(path: Path) -> str:
    """大小写、相对段与符号链接都归一掉再比：同一个可执行文件不该有两种写法。"""
    return os.path.normcase(os.path.realpath(str(path)))


def _terminate(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=_TASKKILL_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _windows_executables(image_name: str) -> dict[int, Path]:
    """遍历进程并读出可执行文件路径；任何一步失败都当作「这个进程看不见」。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.EnumProcesses.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    psapi.EnumProcesses.restype = wintypes.BOOL

    wanted = image_name.casefold()
    found: dict[int, Path] = {}
    for pid in _process_ids(psapi):
        if pid == 0:
            continue
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            length = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(length.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(length)
            ):
                continue
            path = Path(buffer.value)
            if path.name.casefold() == wanted:
                found[pid] = path
        finally:
            kernel32.CloseHandle(handle)
    return found


def _process_ids(psapi: ctypes.WinDLL) -> list[int]:
    """所有进程 PID。枚举在两次调用之间可能变长，所以拿满了就翻倍重来。"""
    capacity = 4096
    while True:
        buffer = (wintypes.DWORD * capacity)()
        returned = wintypes.DWORD()
        if not psapi.EnumProcesses(
            buffer, ctypes.sizeof(buffer), ctypes.byref(returned)
        ):
            return []
        count = returned.value // ctypes.sizeof(wintypes.DWORD)
        if count < capacity:
            return [buffer[index] for index in range(count)]
        capacity *= 2
