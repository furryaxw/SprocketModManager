from __future__ import annotations

import re
from pathlib import PurePosixPath

from ..domain.errors import ScanError

STANDARD_ROOTS = {"Mods", "Plugins", "UserLibs", "UserData"}
ALLOWED_ROOTS = STANDARD_ROOTS | {"AutoTranslator"}

# 供给表里的安装位置写成游戏根目录占位符加相对目录：`{Sprocket}/Mods`、`{Sprocket}/BepInEx/plugins`。
# 单独一个 `{Sprocket}` 表示游戏根目录本身（加载器把自己的载荷放在这里）。
GAME_ROOT_TOKEN = "{Sprocket}"
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
    """把供给表里的位置（`{Sprocket}/Mods`）解析成游戏根目录下的相对目录。

    游戏根目录本身返回 `.`。反斜杠按 `/` 归一，注册表里两种写法都收。
    """
    text = str(value).strip().replace("\\", "/")
    if text != GAME_ROOT_TOKEN and not text.startswith(GAME_ROOT_TOKEN + "/"):
        raise ScanError(f"supply target must start with {GAME_ROOT_TOKEN}: {value!r}")
    rest = text[len(GAME_ROOT_TOKEN):].strip("/")
    if not rest:
        return PurePosixPath(".")
    return validate_relative_path(rest)
