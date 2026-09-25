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
from typing import Any, Callable, Mapping, Sequence

from .manager_paths import state_file_path

LOGGER = logging.getLogger(__name__)

GAME_VERSION_PATH = Path("Sprocket_Data") / "globalgamemanagers"
# 没配好游戏路径或调用方没给目录名单时的兜底：几个运行时的传统根目录。
MOD_DIRECTORIES = ("Mods", "Plugins", "UserLibs", "BepInEx")

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


def game_environment_fingerprint(
        game_path: Path | None,
        directories: Sequence[str] = MOD_DIRECTORIES,
) -> Fingerprint:
    """游戏目录里会影响环境的那些路径的指纹；没配好路径时是个空指纹。

    安装记录文件也在里面：加载器是否装上以那条记录为准，记录一变环境读数就要重来。
    目录名单由调用方给出（活跃标识符的目录），桥接加载器的 `MLLoader/Mods` 也在其中，
    所以那里出现一个模组会改动指纹。
    """
    if game_path is None:
        return ()
    root = Path(game_path)
    signature: list[Any] = [
        str(root),
        _stat_signature(root / GAME_VERSION_PATH),
        _stat_signature(state_file_path(root)),
    ]
    signature.extend(
        (name, mod_directory_signature(root / name)) for name in directories
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
        self._latest_loaders: dict[str, str] = {}
        self._revision = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self._payload is None:
                self._payload = self._read_safely()
            return {
                **self._payload,
                "latest_loaders": dict(self._latest_loaders),
                "revision": self._revision,
            }

    def invalidate(self) -> None:
        """管理器自己刚动过磁盘：下一次读立刻重来，不用等指纹对上。"""
        with self._lock:
            self._payload = None
            self._revision += 1

    def note_latest_loaders(self, mapping: Mapping[str, str]) -> None:
        """记下每个加载器最新可用的版本；未安装时用它当环境值。"""
        cleaned = {str(key): str(value) for key, value in mapping.items() if value}
        with self._lock:
            if self._latest_loaders == cleaned:
                return
            self._latest_loaders = cleaned
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
            return {"sprocket": {}, "loaders": {}}

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
