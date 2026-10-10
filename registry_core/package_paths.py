from __future__ import annotations

import re
from pathlib import PurePosixPath

from .errors import ScanError

STANDARD_ROOTS = {"Mods", "Plugins", "UserLibs", "UserData"}
ALLOWED_ROOTS = STANDARD_ROOTS | {"AutoTranslator"}

# 供给表里的安装位置写成根占位符加相对目录：`{Sprocket}/Mods`、`{Sprocket}/BepInEx/plugins`。
# 单独一个 `{Sprocket}` 表示游戏根目录本身（加载器把自己的载荷放在这里）。
# `{Documents}` 是用户的 Documents 目录：游戏的存档位置（`My Games/Sprocket`）在游戏目录之外。
GAME_ROOT_TOKEN = "{Sprocket}"
DOCUMENTS_ROOT_TOKEN = "{Documents}"
ROOT_TOKENS = (GAME_ROOT_TOKEN, DOCUMENTS_ROOT_TOKEN)
TYPE_WILDCARD = "*"
FILE_TYPE_RE = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9]+)*:[a-z0-9]+(?:[.-][a-z0-9]+)*$"
)
FILE_TYPE_WILDCARD_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*:\*$")


def validate_relative_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or ":" in value:
        raise ScanError(f"unsafe package path: {value!r}")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ScanError(f"unsafe package path: {value!r}")
    return path


def validate_target(value: str) -> PurePosixPath:
    path = validate_relative_path(value)
    if path.parts[0] not in ALLOWED_ROOTS:
        raise ScanError(f"target root is not allowed: {value!r}")
    return path


def file_type_namespace(value: str) -> str:
    """`<加载器>:<类别>` 里的加载器名（`melonloader:mod` -> `melonloader`）。"""
    text = str(value).strip()
    return text.split(":", 1)[0] if ":" in text else ""


def file_type_is_wildcard(value: str) -> bool:
    return FILE_TYPE_WILDCARD_RE.fullmatch(str(value).strip()) is not None


def validate_file_type(value: str) -> str:
    """文件类型：`<加载器>:<类别>`，或通配的 `<加载器>:*`。

    通配类型的最终类别由 DLL 的 PE 元数据决定，只有 `install.files` 的规则能用它；
    供给表里的位置必须落到具体类型上。
    """
    text = str(value).strip()
    if FILE_TYPE_RE.fullmatch(text) or FILE_TYPE_WILDCARD_RE.fullmatch(text):
        return text
    raise ScanError(f"invalid install file type: {value!r}")


def validate_supply_type(value: str) -> str:
    """供给表里的类型必须是具体类型：供给位置不能写成「看情况」。"""
    text = validate_file_type(value)
    if file_type_is_wildcard(text):
        raise ScanError(f"supply type must be concrete: {value!r}")
    return text


def validate_subpath(value: str) -> PurePosixPath:
    """供给目录之下再细分一层时用的相对目录，例如 `MyMod/assets`。"""
    return validate_relative_path(value)


def validate_supply_target(value: str) -> PurePosixPath:
    """把供给表里的位置解析成**记录形态**的相对路径。

    `{Sprocket}/Mods` 落在游戏目录里，返回游戏目录下的相对目录（游戏根目录本身返回 `.`）——
    游戏目录是默认根，所以记录里不带占位符。`{Documents}/My Games/Sprocket` 落在游戏目录之外，
    返回的路径带占位符第一段，由调用方按根解析。反斜杠按 `/` 归一，注册表里两种写法都收。
    """
    text = str(value).strip().replace("\\", "/")
    for token in ROOT_TOKENS:
        if text != token and not text.startswith(token + "/"):
            continue
        rest = text[len(token):].strip("/")
        if token == GAME_ROOT_TOKEN:
            return PurePosixPath(".") if not rest else validate_relative_path(rest)
        parts = validate_relative_path(rest).parts if rest else ()
        return PurePosixPath(token, *parts)
    raise ScanError(f"supply target must start with one of {ROOT_TOKENS}: {value!r}")


def split_managed_root(value: str) -> tuple[str, str]:
    """记录路径拆成 (根占位符, 根内相对路径)。

    游戏目录是默认根：`Mods/X.dll` 读作 `{Sprocket}` 下的 `Mods/X.dll`。其他根以占位符开头，
    必须显式写出来（`{Documents}/My Games/Sprocket/X.json`）。
    """
    text = str(value).strip().replace("\\", "/")
    for token in ROOT_TOKENS:
        if text == token:
            return token, ""
        if text.startswith(token + "/"):
            return token, text[len(token) + 1:]
    return GAME_ROOT_TOKEN, text
