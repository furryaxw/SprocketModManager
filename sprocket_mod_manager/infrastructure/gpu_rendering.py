"""Qt WebEngine 的硬件加速策略：默认开 GPU，起不来过就改软件渲染。

管理器目录里的两个标记文件就是全部状态：

- `gpu-attempt`：这次启动准备走 GPU。UI 真的起来（或正常退出）就删掉它。
- `gpu-disabled`：上一次 GPU 启动没走到那一步，之后默认软件渲染。

`--disable-gpu` 只让这一次走软件渲染；`--enable-gpu` 清掉记录再试一次硬件加速。
"""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)

ATTEMPT_MARKER = "gpu-attempt"
DISABLED_MARKER = "gpu-disabled"


def software_rendering(app_dir: Path, *, force_off: bool = False, force_on: bool = False) -> bool:
    """这次启动要不要走软件渲染。

    走到这里时「上次 GPU 启动没起来」就已经算被记下了：标记还在，说明上一个进程没活到 UI 起来。
    """
    attempt = _marker(app_dir, ATTEMPT_MARKER)
    disabled = _marker(app_dir, DISABLED_MARKER)
    if attempt.is_file():
        attempt.unlink(missing_ok=True)
        disabled.touch()
        LOGGER.warning("the previous start did not get the WebView up with hardware acceleration")
    if force_on:
        disabled.unlink(missing_ok=True)
        LOGGER.info("trying hardware acceleration again")
        return False
    if force_off:
        return True
    if disabled.is_file():
        LOGGER.info("hardware acceleration stays off after a failed start; --enable-gpu tries it again")
        return True
    return False


def mark_attempt(app_dir: Path) -> Path:
    """写下「这次在试 GPU」；返回要在成功时删掉的那个路径。"""
    path = _marker(app_dir, ATTEMPT_MARKER)
    path.touch()
    return path


def record_failure(app_dir: Path) -> None:
    """GPU 这条路走不通：删掉进行中的标记，记下一次失败。"""
    _marker(app_dir, ATTEMPT_MARKER).unlink(missing_ok=True)
    _marker(app_dir, DISABLED_MARKER).touch()


def _marker(app_dir: Path, name: str) -> Path:
    directory = Path(app_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / name
