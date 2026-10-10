"""Windows 已知文件夹：`{Documents}` 根的实际位置。

`Documents` 会被 OneDrive 之类重定向，拼 `%USERPROFILE%\\Documents` 会指到错的位置，
所以走 `SHGetKnownFolderPath(FOLDERID_Documents)`。解析结果进程内只算一次。
其他平台（开发机上跑客户端）退回 `~/Documents`。
"""

from __future__ import annotations

import ctypes
import os
from functools import lru_cache
from pathlib import Path

from ..domain.errors import InstallError

# FOLDERID_Documents
DOCUMENTS_FOLDER_ID = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_ulong),
        ("data2", ctypes.c_ushort),
        ("data3", ctypes.c_ushort),
        ("data4", ctypes.c_ubyte * 8),
    ]


@lru_cache(maxsize=1)
def documents_directory() -> Path:
    """用户的 Documents 目录；解析不出来就报错，不猜一个位置出来。"""
    if os.name != "nt":
        return Path.home() / "Documents"
    try:
        ole32 = ctypes.windll.ole32
        shell32 = ctypes.windll.shell32
        guid = _Guid()
        ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_Guid)]
        ole32.CLSIDFromString.restype = ctypes.c_long
        if ole32.CLSIDFromString(DOCUMENTS_FOLDER_ID, ctypes.byref(guid)) != 0:
            raise InstallError("cannot resolve the Documents known folder")
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(_Guid),
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        pointer = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(pointer)) != 0:
            raise InstallError("cannot resolve the Documents known folder")
        try:
            return Path(pointer.value)
        finally:
            ole32.CoTaskMemFree(ctypes.cast(pointer, ctypes.c_void_p))
    except (AttributeError, OSError) as exc:
        raise InstallError(f"cannot resolve the Documents known folder: {exc}") from exc
