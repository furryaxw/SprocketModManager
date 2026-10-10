"""管理器缓存目录（`<app_dir>/cache`）的布局与清理。

这里只有能再取回来的东西：网络文档（`http/`，`HttpClient` 按 `(url, accept)` 键存的响应）、
索引供给表（`providers.json`）、诊断规则包（`diagnosis.json`）。删掉之后，下次用到时重新联网取。

游戏目录里的 DLL 元数据缓存不在这个目录：它属于某个游戏目录
（`<game>/SprocketModManager/file-metadata.json`，见 `manager_paths`）。删它要连内存里那份镜像
一起丢，否则这一会话还会按旧读数回答、并把旧条目写回新文件。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .dll_metadata import clear_metadata_cache
from .manager_paths import file_metadata_path

CACHE_DIR_NAME = "cache"
HTTP_CACHE_DIR_NAME = "http"


@dataclass(frozen=True)
class CacheRemoval:
    """一次清理删掉了什么：调用方拿它回话（文件数 + 字节数）。"""

    files: int
    bytes_removed: int


def app_cache_dir(app_dir: Path | str) -> Path:
    """`<app_dir>/cache`。"""
    return Path(app_dir).expanduser() / CACHE_DIR_NAME


def http_cache_dir(app_dir: Path | str) -> Path:
    """`<app_dir>/cache/http`：网络文档缓存。"""
    return app_cache_dir(app_dir) / HTTP_CACHE_DIR_NAME


def clear_http_cache(app_dir: Path | str) -> CacheRemoval:
    """删掉网络文档缓存，保留供给表与诊断规则包。"""
    return _remove_tree(http_cache_dir(app_dir))


def clear_app_cache(app_dir: Path | str) -> CacheRemoval:
    """删掉整个缓存目录。"""
    return _remove_tree(app_cache_dir(app_dir))


def clear_file_metadata_cache(game_dir: Path | str) -> CacheRemoval:
    """删掉这个游戏目录的 DLL 元数据缓存，并丢掉内存里那份镜像。"""
    removed = _remove_file(file_metadata_path(Path(game_dir)))
    clear_metadata_cache(include_disk=True)
    return removed


def _remove_tree(path: Path) -> CacheRemoval:
    if not path.is_dir():
        return CacheRemoval(0, 0)
    files, size = _measure(path)
    shutil.rmtree(path, ignore_errors=True)
    return CacheRemoval(files, size)


def _remove_file(path: Path) -> CacheRemoval:
    try:
        size = path.stat().st_size
    except OSError:
        return CacheRemoval(0, 0)
    try:
        path.unlink()
    except FileNotFoundError:
        return CacheRemoval(0, 0)
    return CacheRemoval(1, size)


def _measure(path: Path) -> tuple[int, int]:
    files = 0
    size = 0
    for entry in path.rglob("*"):
        if not entry.is_file():
            continue
        files += 1
        try:
            size += entry.stat().st_size
        except OSError:
            continue
    return files, size
