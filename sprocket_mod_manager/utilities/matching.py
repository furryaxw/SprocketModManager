"""`match` 模式的匹配口径。

模式匹配的是**压缩包内路径**（`/` 分隔）：`*` 与 `?` 只在当前一层里匹配（不跨 `/`），`**` 独占
一段时匹配任意层（含零层，所以 `**/x` 也命中根上的 `x`）。大小写不敏感，段内还认 glob 的
`[...]` 字符组。没有「按文件名匹配」的兜底：写 `*.dll` 就是压缩包根目录下的 DLL，子目录里的要写
`**/*.dll`。
"""

from __future__ import annotations

import re
from functools import lru_cache

# 段内通配符：`*`/`?` 不跨 `/`；`[` 起字符组。
WILDCARD_CHARS = "*?["


def _segment_regex(segment: str) -> str:
    """一段里的通配符翻成正则：`*`/`?` 不跨 `/`，`[...]` 与 glob 同义。"""
    out: list[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = index + 1
            if end < len(segment) and segment[end] in "!^":
                end += 1
            if end < len(segment) and segment[end] == "]":
                end += 1
            while end < len(segment) and segment[end] != "]":
                end += 1
            if end >= len(segment):
                out.append(re.escape("["))
            else:
                body = segment[index + 1:end].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                index = end + 1
                continue
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    segments = str(pattern).replace("\\", "/").split("/")
    pieces: list[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            # 独占一段：`**` 在末尾吃剩下的全部，在中间吃任意层（含零层）。
            pieces.append(".*" if last else "(?:[^/]+/)*")
            continue
        pieces.append(_segment_regex(segment))
        if not last:
            pieces.append("/")
    return re.compile("".join(pieces), re.IGNORECASE)


def matches(pattern: str, path: str) -> bool:
    """`path`（压缩包内路径）是否命中这条模式。"""
    return _compiled(str(pattern)).fullmatch(str(path).replace("\\", "/")) is not None


def fixed_directory(pattern: str) -> tuple[str, ...]:
    """模式开头那段固定目录：逐段取到第一个含通配符的段为止；最后一段是文件名，不算目录。

    `layout: "tree"` 用它当锚点：命中条目的这段路径对应落点里的 `subpath`。
    """
    segments = str(pattern).replace("\\", "/").split("/")
    fixed: list[str] = []
    for segment in segments[:-1]:
        if not segment or any(char in segment for char in WILDCARD_CHARS):
            break
        fixed.append(segment)
    return tuple(fixed)
