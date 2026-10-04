from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

SPROCKET_EXECUTABLE = "Sprocket.exe"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TASKKILL_TIMEOUT_SECONDS = 10
_TERMINATE_WAIT_SECONDS = 10
_FORCE_TERMINATE_WAIT_SECONDS = 1
_TERMINATE_POLL_SECONDS = 0.1
_PROC = Path("/proc")
_WINDOWS_DRIVE = re.compile(r"^([A-Za-z]):[\\/](.*)$", re.DOTALL)


def running_executables(image_name: str) -> dict[int, Path]:
    """本机正在跑的 `image_name` 进程：PID → 可执行文件完整路径。

    路径读不到的进程（权限不足等）不列出来 —— 判定「某个目录里的程序在不在跑」时，
    一个位置未知的同名进程不能算数。
    """
    if os.name == "nt":
        return _windows_executables(image_name)
    return _linux_executables(image_name)


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
    if os.name != "nt":
        return _terminate_wine(pid)
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


def _linux_executables(image_name: str) -> dict[int, Path]:
    """Linux 上没有 psapi：从 `/proc` 的命令行里认出 Wine/Proton 跑起来的那个 exe。

    `<pid>/exe` 指向 wine 的加载器，游戏本体只出现在命令行里，所以命令行与 cwd 都要看。
    """
    wanted = image_name.casefold()
    found: dict[int, Path] = {}
    try:
        entries = os.listdir(_PROC)
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            cmdline = (_PROC / entry / "cmdline").read_bytes()
        except OSError:
            continue
        try:
            working_directory = Path(os.readlink(_PROC / entry / "cwd"))
        except OSError:
            working_directory = None
        image = _wine_image(cmdline, working_directory, wanted)
        if image is not None:
            found[int(entry)] = image
    return found


def _wine_image(cmdline: bytes, working_directory: Path | None, wanted: str) -> Path | None:
    for token in cmdline.split(b"\0"):
        candidate = _wine_path(os.fsdecode(token), working_directory)
        if candidate is not None and candidate.name.casefold() == wanted:
            return candidate
    return None


def _wine_path(token: str, working_directory: Path | None) -> Path | None:
    """命令行里的一个参数 → 它在 Linux 上的路径。

    Proton 给的是 unix 路径、`Z:\\...`（wine 把 `/` 挂成 Z:），也可能给相对于 cwd 的名字；
    别的盘符是 Proton 前缀内部的路径，和游戏目录对不上，不看。
    """
    text = token.strip().strip('"')
    if not text:
        return None
    drive = _WINDOWS_DRIVE.match(text)
    if drive is not None:
        if drive.group(1).casefold() != "z":
            return None
        return Path("/" + drive.group(2).replace("\\", "/"))
    path = Path(text)
    if path.is_absolute():
        return path
    return working_directory / path if working_directory is not None else None


def _terminate_wine(pid: int) -> bool:
    """先请游戏自己退，超时还在就强杀；返回它是否真的没了。"""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    if _wait_for_exit(pid, _TERMINATE_WAIT_SECONDS):
        return True
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    return _wait_for_exit(pid, _FORCE_TERMINATE_WAIT_SECONDS)


def _wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if not _process_alive(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_TERMINATE_POLL_SECONDS)


def _process_alive(pid: int) -> bool:
    """僵尸进程算已经退出：它的 `/proc` 项还在，只是在等父进程收尸。"""
    try:
        stat = (_PROC / str(pid) / "stat").read_bytes()
    except OSError:
        return False
    try:
        return stat.rsplit(b")", 1)[1].split()[0] != b"Z"
    except IndexError:
        return True
