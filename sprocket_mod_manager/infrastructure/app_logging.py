from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

LATEST_LOG_NAME = "Latest.log"
HISTORY_LIMIT = 10
LOGGER = logging.getLogger(__name__)


def manager_log_path(app_dir: Path) -> Path:
    return app_dir.expanduser() / LATEST_LOG_NAME


def manager_history_dir(app_dir: Path) -> Path:
    return app_dir.expanduser() / "logs"


def _close_manager_handlers() -> None:
    root = logging.getLogger()
    for handler in tuple(root.handlers):
        if getattr(handler, "_sprocket_manager_handler", False):
            root.removeHandler(handler)
            handler.close()


def rotate_logs(app_dir: Path, *, history_limit: int = HISTORY_LIMIT) -> Path:
    """轮转 `Latest.log`（历史放 `logs/`，最多留 `history_limit` 份）。

    **另一个进程正开着这个日志时不轮转、也不清空**：管理器 GUI 会一直持有 `Latest.log`，
    这时命令行启动如果硬要 rename 就会 `WinError 32` 直接崩掉（命令行整条命令都跑不起来）。
    这种情况下直接往同一个文件里追加，宁可少一份历史，也不能让命令挂掉。
    """
    app_dir = app_dir.expanduser()
    app_dir.mkdir(parents=True, exist_ok=True)
    history_dir = manager_history_dir(app_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    latest = manager_log_path(app_dir)
    if latest.is_file() and latest.stat().st_size:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        try:
            latest.replace(history_dir / f"{stamp}.log")
        except OSError as exc:
            LOGGER.warning("log rotation skipped (the log is in use): %s", exc)
            return latest
    else:
        latest.write_text("", encoding="utf-8")

    histories = sorted(
        history_dir.glob(f"*.log"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for expired in histories[max(0, history_limit):]:
        try:
            expired.unlink()
        except OSError as exc:
            LOGGER.warning("could not drop an expired log: %s", exc)
    latest.write_text("", encoding="utf-8")
    return latest


def _terminal_handler(level: int, formatter: logging.Formatter) -> logging.Handler | None:
    """终端那一份。窗口程序没有 `stderr`（`sys.stderr is None`），那时只剩文件。"""
    if sys.stderr is None:
        return None
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler._sprocket_manager_handler = True  # type: ignore[attr-defined]
    return handler


def configure_logging(
        app_dir: Path,
        *,
        debug: bool = False,
) -> Path:
    """两份都装：文件留档，终端同时能看到同一条。"""
    _close_manager_handlers()
    latest = rotate_logs(app_dir)
    level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(latest, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler._sprocket_manager_handler = True  # type: ignore[attr-defined]

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    terminal_handler = _terminal_handler(level, formatter)
    if terminal_handler is not None:
        root.addHandler(terminal_handler)
    logging.captureWarnings(True)
    return latest


def set_logging_level(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        if getattr(handler, "_sprocket_manager_handler", False):
            handler.setLevel(level)
