"""本机 Sprocket 版本：从游戏目录里的 Unity 资源读，不联网。

版本号是 Unity 序列化的 `PlayerSettings.bundleVersion`，藏在 `Sprocket_Data/globalgamemanagers`
里，**不是** exe 的版本资源（那个写的是 Unity 自己的版本，例如 `2022.3.62f2`）。
字段在文件头部（实测偏移 ~2 KB），所以只读前 1 MiB 就够。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VERSION_FILE = "globalgamemanagers"
VERSION_DIR = "Sprocket_Data"
READ_LIMIT = 1 << 20

# 本机格式（0.2.53.2）。先试它：在真实文件里头部范围内只命中一次，误抓风险最低。
RELEASE_PATTERN = re.compile(rb"0\.\d+\.\d+\.\d+")
# 万一将来游戏走到 1.x，仍然能读出来（放在本机格式之后，避免放宽常见情况的误抓率）。
GENERIC_PATTERN = re.compile(rb"(?<![\d.])\d+\.\d+\.\d+\.\d+(?![\d.])")
# 旧格式（0.127 之类）：只在拿不到 4 段版本时才当作「太老」的依据。
LEGACY_PATTERN = re.compile(rb"(?<![\d.])0\.\d{2,4}(?:\.\d{1,3})?(?![\d.])")

STATE_OK = "ok"
STATE_LEGACY = "legacy"
STATE_UNREADABLE = "unreadable"
STATE_UNCONFIGURED = "unconfigured"


@dataclass(frozen=True)
class GameVersion:
    """一次读取的结果；`state` 是给界面看的四态，`raw` 保留抓到的原文以便核对。"""

    state: str
    version: str = ""
    raw: str = ""
    source: str = ""
    detail: str = ""

    @staticmethod
    def unconfigured() -> "GameVersion":
        return GameVersion(STATE_UNCONFIGURED, detail="尚未配置 Sprocket 路径")

    def as_dict(self) -> dict[str, str]:
        return {
            "state": self.state,
            "version": self.version,
            "raw": self.raw,
            "source": self.source,
            "detail": self.detail,
        }


def read_game_version(game_dir: Path) -> GameVersion:
    """读游戏版本；读不到不是异常，而是 `unreadable`/`legacy` 状态。"""
    path = Path(game_dir).expanduser() / VERSION_DIR / VERSION_FILE
    source = f"{VERSION_DIR}/{VERSION_FILE}"
    try:
        with path.open("rb") as handle:
            head = handle.read(READ_LIMIT)
    except OSError as exc:
        return GameVersion(STATE_UNREADABLE, source=source, detail=f"读不到 {source}：{exc.strerror or exc}")

    release = RELEASE_PATTERN.search(head) or GENERIC_PATTERN.search(head)
    if release is not None:
        text = release.group().decode("ascii", "replace")
        return GameVersion(STATE_OK, version=text, raw=text, source=source)

    legacy = LEGACY_PATTERN.search(head)
    if legacy is not None:
        text = legacy.group().decode("ascii", "replace")
        return GameVersion(
            STATE_LEGACY,
            raw=text,
            source=source,
            detail=f"检测到旧格式版本 {text}，这个版本太老，没有模组声明支持它",
        )

    return GameVersion(STATE_UNREADABLE, source=source, detail=f"{source} 里没有版本号")
