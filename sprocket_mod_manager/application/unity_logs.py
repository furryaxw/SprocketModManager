"""Unity 自己的日志：游戏崩在加载器之前时，这是唯一还留着证据的地方。

日志不在游戏目录里，而在 `%USERPROFILE%\\AppData\\LocalLow\\<公司>\\<产品>\\Player.log`。
公司和产品名从 `<游戏目录>/*_Data/app.info` 的两行读出来（Unity 自己写的），
所以这里不在公司名上写死任何东西。

只认 `Player.log` 这一份：它是**最近一次**运行写下的。同一目录下的 `Player-prev.log`
与游戏目录下的 `MelonLoader/Logs/*.log` 都是更早的会话，诊断不看那些。
"""

from __future__ import annotations

import os
from pathlib import Path

from .identifiers import LogSource

APP_INFO_NAME = "app.info"
PLAYER_LOG_NAME = "Player.log"
UNITY_LOG_ID = "unity"


def _app_info_path(game_path: Path) -> Path | None:
    """`<游戏目录>/*_Data/app.info`：Unity 把公司名和产品名写在这里。

    按 `app.info` 在不在来认数据目录，而不是按可执行文件名去拼 —— 游戏目录里还躺着
    `UnityCrashHandler64.exe` 这类东西。
    """
    candidates = sorted(Path(game_path).glob(f"*_Data/{APP_INFO_NAME}"))
    return candidates[0] if candidates else None


def unity_company_product(game_path: Path | None) -> tuple[str, str] | None:
    """`app.info` 的前两行：公司名、产品名。读不到或不足两行时返回 None。"""
    if game_path is None:
        return None
    app_info = _app_info_path(Path(game_path))
    if app_info is None:
        return None
    try:
        lines = app_info.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    values = [line.strip() for line in lines if line.strip()]
    if len(values) < 2 or "/" in values[0] or "/" in values[1] or "\\" in values[0] or "\\" in values[1]:
        # 这两行会直接当目录名用，带上分隔符就不是公司名/产品名了。
        return None
    return values[0], values[1]


def local_low_root() -> Path | None:
    """`%USERPROFILE%\\AppData\\LocalLow`；环境变量读不到时没有 Unity 日志可定位。"""
    profile = os.environ.get("USERPROFILE")
    return Path(profile) / "AppData" / "LocalLow" if profile else None


def player_log_path(game_path: Path | None) -> Path | None:
    """Unity 的 `Player.log` 完整路径；定位不出来时返回 None。

    定位失败不是错误：游戏可能没有 `app.info`，或者这一局还没跑过、LocalLow 下还没有那个目录。
    """
    names = unity_company_product(game_path)
    root = local_low_root()
    if names is None or root is None:
        return None
    company, product = names
    return root / company / product / PLAYER_LOG_NAME


def unity_log_sources(game_path: Path | None) -> tuple[LogSource, ...]:
    """Unity 日志来源；路径定位不出来时是空表 —— 连路径都不知道就没什么可列的。"""
    path = player_log_path(game_path)
    if path is None:
        return ()
    return (LogSource(id=UNITY_LOG_ID, loader="Unity", path=str(path)),)
