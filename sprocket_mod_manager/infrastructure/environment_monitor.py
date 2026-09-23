"""环境监听：游戏目录里那几个文件的轻量指纹，变了就重新读环境并让 `revision` 加一。

前端每秒问一次 `get_environment`，靠 `revision` 判断要不要重画，所以读数在这里缓存，
那一秒一次的问询不产生文件读取。用轮询而不是文件系统事件：零新依赖、不怕句柄与权限、
目录被删掉也不会让线程死掉，和安装队列的轮询是同一套路子。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

GAME_VERSION_PATH = Path("Sprocket_Data") / "globalgamemanagers"
LOADER_PROXY = "version.dll"
LOADER_ROOT = "MelonLoader"
MOD_DIRECTORIES = ("Mods", "Plugins", "UserLibs")

Fingerprint = tuple[Any, ...]


def _stat_signature(path: Path) -> tuple[str, int | None, int | None]:
    try:
        stat = path.stat()
    except OSError:
        return (path.name, None, None)
    return (path.name, stat.st_mtime_ns, stat.st_size)


def mod_directory_signature(directory: Path) -> tuple[Any, ...]:
    """目录**第 1 层**的（名字, mtime, 大小）：和「只扫 1 层」的规则一致，不递归。"""
    try:
        with os.scandir(directory) as entries:
            return tuple(
                sorted(
                    (entry.name, _stat_signature(Path(entry.path))[1], _stat_signature(Path(entry.path))[2])
                    for entry in entries
                )
            )
    except OSError:
        return ()


def game_environment_fingerprint(game_path: Path | None) -> Fingerprint:
    """游戏目录里会影响环境的那些路径的指纹；没配好路径时是个空指纹。"""
    if game_path is None:
        return ()
    root = Path(game_path)
    signature: list[Any] = [str(root), _stat_signature(root / GAME_VERSION_PATH), _stat_signature(root / LOADER_PROXY)]
    loader_root = root / LOADER_ROOT
    try:
        cores = sorted(_stat_signature(path) for path in loader_root.glob("net*/MelonLoader.dll"))
    except OSError:
        cores = []
    signature.append(tuple(cores))
    signature.extend(
        (name, mod_directory_signature(root / name)) for name in MOD_DIRECTORIES
    )
    return tuple(signature)


class EnvironmentMonitor:
    """缓存环境读数 + 指纹轮询。`read` 只读本地文件，不联网。"""

    def __init__(
        self,
        read: Callable[[], dict[str, Any]],
        fingerprint: Callable[[], Fingerprint],
        *,
        interval: float = 1.0,
        silence: float = 0.4,
    ) -> None:
        self._read = read
        self._fingerprint = fingerprint
        self._interval = interval
        self._silence = silence
        self._lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._latest_loader: str | None = None
        self._revision = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self._payload is None:
                self._payload = self._read_safely()
            return {
                **self._payload,
                "latest_loader": self._latest_loader,
                "revision": self._revision,
            }

    def invalidate(self) -> None:
        """管理器自己刚动过磁盘：下一次读立刻重来，不用等指纹对上。"""
        with self._lock:
            self._payload = None
            self._revision += 1

    def note_latest_loader(self, version: str) -> None:
        """记下「最新可用的 MelonLoader 版本」，未安装时用它当环境值。"""
        with self._lock:
            if self._latest_loader == version:
                return
            self._latest_loader = version
            self._revision += 1

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="sprocket-environment", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)

    def _read_safely(self) -> dict[str, Any]:
        try:
            return self._read()
        except Exception:  # noqa: BLE001 - 监听线程和接口都不能因为一次读取失败而倒下
            LOGGER.exception("environment read failed")
            return {"sprocket": {}, "melonloader": {"installed": False, "version": None}}

    def _safe_fingerprint(self) -> Fingerprint:
        try:
            return self._fingerprint()
        except OSError:
            return ()

    def _loop(self) -> None:
        previous = self._safe_fingerprint()
        while not self._stop.wait(self._interval):
            current = self._safe_fingerprint()
            if current == previous:
                continue
            previous = current
            # 安装/卸载会连着改几十个文件：静默一小会儿，再以最后的状态为准。
            if self._stop.wait(self._silence):
                return
            previous = self._safe_fingerprint()
            self.invalidate()
