from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

LATEST_LOG_NAME = "Latest.log"
HISTORY_LIMIT = 10


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
    app_dir = app_dir.expanduser()
    app_dir.mkdir(parents=True, exist_ok=True)
    history_dir = manager_history_dir(app_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    latest = manager_log_path(app_dir)
    if latest.is_file() and latest.stat().st_size:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        history = latest.replace(history_dir / f"{stamp}.log")
        history.touch()
    else:
        latest.write_text("", encoding="utf-8")

    histories = sorted(
        history_dir.glob(f"*.log"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for expired in histories[max(0, history_limit):]:
        expired.unlink()
    latest.write_text("", encoding="utf-8")
    return latest


def configure_logging(
        app_dir: Path,
        *,
        debug: bool = False,
        console: bool = False,
) -> Path:
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
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        console_handler._sprocket_manager_handler = True  # type: ignore[attr-defined]
        root.addHandler(console_handler)
    logging.captureWarnings(True)
    return latest


def set_logging_level(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        if getattr(handler, "_sprocket_manager_handler", False):
            handler.setLevel(level)
