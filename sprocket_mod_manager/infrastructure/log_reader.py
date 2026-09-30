"""把一份日志读成带**真实行号**的行，供诊断的签名匹配使用。

行号必须对得上文件本身：报告里的证据要能让用户翻到那一行，所以任何取舍都只丢行、不改号。

超过行数上限时**头尾各留一段**：出事的证据要么在启动阶段（`Player.log` 最前面那些引擎初始化
与加载器引导），要么在结束前（崩溃前的最后几行），中间是重复的运行期刷屏。只留尾部会把
「游戏根本起不来」这类诊断整体丢掉 —— 那正是这份功能最该覆盖的场景。

只读调用方点名的那一份文件；轮转出来的历史日志不在这个模块的职责里。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 超过这个大小根本不去读：日志是别人写的文件，读进来之前先看它多大。
MAX_BYTES = 8 * 1024 * 1024
# 匹配跑在这些行上。真实的加载器日志在几百 KB 量级、`Player.log` 在几 MB 量级，
# 所以正常大小的日志整份都读得到，只有病态大的才会走到下面那对头尾窗口。
MAX_LINES = 200000
# 真要取舍时开头留多少行：引擎初始化与加载器引导都在这几行里。
HEAD_LINES = 20000

READ_OK = ""
READ_MISSING = "missing"
READ_TOO_LARGE = "too_large"
READ_UNREADABLE = "unreadable"


@dataclass(frozen=True)
class LogText:
    """一份日志的读取结果：行、结果码、以及有没有丢掉中间一段。"""

    lines: tuple[dict[str, Any], ...]
    status: str
    truncated: bool

    @property
    def readable(self) -> bool:
        return self.status == READ_OK


def _window(raw: list[str]) -> tuple[list[tuple[int, str]], bool]:
    """要保留的行 -> （行号, 正文）的有序表，以及有没有丢掉中间一段。"""
    total = len(raw)
    if total <= MAX_LINES:
        return [(number, text) for number, text in enumerate(raw, start=1)], False
    tail_lines = MAX_LINES - HEAD_LINES
    head = [(number, text) for number, text in enumerate(raw[:HEAD_LINES], start=1)]
    # 下标从 0 起、行号从 1 起，尾段第一行的行号是「它前面有多少行」加一。
    tail_start = total - tail_lines + 1
    tail = [
        (tail_start + offset, text)
        for offset, text in enumerate(raw[total - tail_lines:])
    ]
    return head + tail, True


def read_log_lines(path: Path | None, *, role: str, source: str) -> LogText:
    """读一份日志。读不到不是错误，是「这份日志没有」—— 由结果码表达。"""
    if path is None:
        return LogText((), READ_MISSING, False)
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return LogText((), READ_MISSING, False)
    if size > MAX_BYTES:
        # 宁可说「这份太大没读」，也不要交出一份行号对不上的证据。
        return LogText((), READ_TOO_LARGE, True)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return LogText((), READ_UNREADABLE, False)

    window, truncated = _window(text.splitlines())
    lines = tuple(
        {"role": role, "source": source, "number": number, "text": item}
        for number, item in window
    )
    return LogText(lines, READ_OK, truncated)
