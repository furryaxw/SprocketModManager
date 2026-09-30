"""把一份日志读成**带真实行号**的行流，供诊断的签名匹配使用。

行号必须对得上文件本身：报告里的证据要能让用户翻到那一行，所以这里只丢内容、不改号。

一次只持有当前这一行：日志是别人写的文件，模组刷屏或长时间运行会有几百 MB，所以既不整份读、
也不设"太大就不读"。收住规模的是下游那两处 —— 匹配时的行内滑窗，和每条规则的闸门。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

READ_OK = ""
READ_MISSING = "missing"
READ_UNREADABLE = "unreadable"


class LogStream:
    """一份日志的行流。

    `status` 在打开时就定了，`count` 随着行一起推进。读完（或提前收手）之后 `close()`，再把
    `reading()` 交给报告 —— 那份读数说的是这份日志**实际发生了什么**，不是猜的。
    """

    def __init__(self, path: Path | None, *, role: str, source: str) -> None:
        self.role = role
        self.source = source
        self.path = Path(path) if path is not None else None
        self.status = READ_MISSING
        self.count = 0
        self._handle: Any = None

    def __enter__(self) -> "LogStream":
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def open(self) -> None:
        """打开文件；打不开不是错误，而是这份日志的一个状态。"""
        if self.path is None:
            return
        try:
            self._handle = self.path.open("r", encoding="utf-8", errors="replace")
        except FileNotFoundError:
            self.status = READ_MISSING
        except OSError:
            self.status = READ_UNREADABLE
        else:
            self.status = READ_OK

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if self._handle is None:
            return
        for number, text in enumerate(self._handle, start=1):
            self.count = number
            yield {
                "role": self.role,
                "source": self.source,
                "number": number,
                "text": text.rstrip("\r\n"),
            }

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def reading(self) -> dict[str, Any]:
        """报告里每份日志的读数：它是什么、在哪、什么状态、读到了多少行。"""
        return {
            "role": self.role,
            "label": self.source,
            "path": str(self.path) if self.path is not None else "",
            "status": self.status,
            "lines": self.count,
        }
