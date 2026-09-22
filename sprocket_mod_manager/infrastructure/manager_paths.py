"""游戏目录里的管理器状态路径（状态文件 / 元数据缓存 / 备份）。

单独一个模块是为了避开循环导入：`profiles.py` 需要 `Installer`，而 `installer.py` 需要这些路径。

规则：**AppData 只放管理器自身的配置**（config.json、网络缓存、日志、WebView 存储）。
凡是从游戏目录读出来、或要写回游戏目录的东西（安装记录、DLL 元数据缓存、被覆盖文件的备份）
一律留在游戏目录下的 `SprocketModManager/`：
换机或整体备份游戏目录时状态跟着走，AppData 里也不会堆积"游戏里的东西"。
"""

from __future__ import annotations

from pathlib import Path

STATE_DIR_NAME = "SprocketModManager"
STATE_FILE_NAME = "installed.json"
FILE_METADATA_FILE_NAME = "file-metadata.json"
SUPPRESSION_FILE_NAME = "suppression.json"
BACKUP_DIR_NAME = "backup"


def manager_state_dir(game_dir: Path) -> Path:
    """`<game>/SprocketModManager`。"""
    return game_dir.expanduser().resolve() / STATE_DIR_NAME


def state_file_path(game_dir: Path) -> Path:
    return manager_state_dir(game_dir) / STATE_FILE_NAME


def file_metadata_path(game_dir: Path) -> Path:
    """DLL 元数据缓存文件。"""
    return manager_state_dir(game_dir) / FILE_METADATA_FILE_NAME


def suppression_path(game_dir: Path) -> Path:
    """「抑制损坏提示」名单：描述这个游戏目录里的文件，所以跟状态一起留在游戏目录。"""
    return manager_state_dir(game_dir) / SUPPRESSION_FILE_NAME


def backups_dir(game_dir: Path) -> Path:
    """被覆盖/删除的游戏文件的备份目录：`<game>/SprocketModManager/backup`。"""
    return manager_state_dir(game_dir) / BACKUP_DIR_NAME
