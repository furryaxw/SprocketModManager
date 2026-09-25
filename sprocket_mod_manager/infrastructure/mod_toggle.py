"""启用/禁用磁盘上模组 DLL 的约定实现。

契约见 `G:\\Sprocket\\mod\\SprocketModAPI\\docs\\mod-metadata.md`：禁用 = 把 ``X.dll``
改名为 ``X.dll.disable``（MelonLoader 只加载 ``*.dll``），重启后生效。管理器和游戏内菜单
使用完全相同的规则，所以这里只做重命名，不加载程序集、不写状态文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..domain.errors import ModToggleError

DISABLE_SUFFIX = ".disable"
DLL_SUFFIX = ".dll"

# MelonLoader 在没有桥接加载器时把 Mods / Plugins 里的程序集当作模组加载；UserLibs 是可以被
# 引用的库，就地改名可能破坏依赖它的模组。标识符激活后给出的目录（桥接加载器的 `MLLoader/Mods`
# 等）取代这份默认名单，判据仍然是「标识符声明这个类型可切换」。
TOGGLE_ROOTS = ("Mods", "Plugins")


def is_disabled_path(path: Path | str) -> bool:
    name = Path(path).name.casefold()
    return name.endswith(DLL_SUFFIX + DISABLE_SUFFIX)


def is_loadable_path(path: Path | str) -> bool:
    name = Path(path).name.casefold()
    return name.endswith(DLL_SUFFIX) and not name.endswith(DISABLE_SUFFIX)


def disabled_path_for(path: Path) -> Path:
    return path.with_name(path.name + DISABLE_SUFFIX)


def enabled_path_for(path: Path) -> Path:
    if not is_disabled_path(path):
        return path
    return path.with_name(path.name[: -len(DISABLE_SUFFIX)])


def canonical_relative(relative: str) -> str:
    """把 `Mods/X.dll.disable` 归一成 `Mods/X.dll`：`.dll` 与 `.dll.disable` 是**同一个文件**。

    状态文件里一律记规范路径（`.dll`）+ 条目里的 `disabled` 标志，这样启用/禁用不需要搬键，
    也不会因为一次手工改名就留下孤儿条目。
    """
    text = str(relative).replace("\\", "/")
    if text.casefold().endswith(DLL_SUFFIX + DISABLE_SUFFIX):
        return text[: -len(DISABLE_SUFFIX)]
    return text


def direct_files(directory: Path) -> list[Path]:
    """目录 **1 层** 内的文件与符号链接（不进入子目录）；目录不存在或不可读时返回空列表。

    模组扫描只认受管根目录的直接子文件：子目录里的 DLL 不进清单、不被认领、
    也不出现在禁用列表里，否则会出现「扫描看不到、记录里却有」的两份事实。
    """
    if not directory.is_dir():
        return []
    try:
        return [
            path
            for path in directory.iterdir()
            if path.is_file() or path.is_symlink()
        ]
    except OSError:
        return []


def find_disabled_dlls(directory: Path) -> list[Path]:
    """列出目录 **1 层** 内的 ``*.dll.disable``。"""
    found = [path for path in direct_files(directory) if is_disabled_path(path)]
    return sorted(found, key=lambda item: str(item).casefold())


def directory_prefix(relative: str) -> tuple[str, ...]:
    """相对路径里文件所在的目录（`Mods/X.dll` -> `("Mods",)`）。"""
    parts = tuple(part for part in str(relative).replace("\\", "/").split("/") if part)
    return parts[:-1]


def is_in_roots(relative: str, roots: Sequence[str]) -> bool:
    """`relative` 是否落在某个允许目录之下。

    单段目录（`Mods`）覆盖它下面的所有层级，多段目录（`MLLoader/Mods`）按整段前缀匹配：
    目录名单由标识符给出，桥接加载器的目录就在游戏根的下一层。
    """
    directory = directory_prefix(relative)
    for root in roots:
        root_parts = tuple(part for part in str(root).replace("\\", "/").split("/") if part)
        if root_parts and directory[: len(root_parts)] == root_parts:
            return True
    return False


def resolve_mod_path(game_path: Path, path: str, roots: Sequence[str] = TOGGLE_ROOTS) -> Path:
    """把 CLI/GUI 传来的相对路径解析成游戏目录内的 DLL 路径。

    绝对路径、``..``、不在允许目录下的路径以及非 ``.dll`` / ``.dll.disable`` 文件名一律抛
    :class:`ModToggleError`。``roots`` 是允许的目录名单，默认是 MelonLoader 没有桥接时的
    `Mods` / `Plugins`；调用方用标识符算出来的可切换目录替换它。
    """
    if not path or not str(path).strip():
        raise ModToggleError("缺少模组路径")

    game_root = Path(game_path)
    relative = Path(str(path).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ModToggleError("模组路径必须位于游戏目录内")

    target = game_root / relative
    try:
        target.relative_to(game_root)
    except ValueError as exc:
        raise ModToggleError("模组路径越出游戏目录") from exc
    if not is_in_roots(relative.as_posix(), roots):
        raise ModToggleError("只能切换受管目录下的 DLL")
    if not is_disabled_path(target) and not is_loadable_path(target):
        raise ModToggleError(f"只能切换 .dll 或 .dll.disable 文件：{target.name}")
    return target


def set_enabled(path: Path, enabled: bool) -> Path:
    """按约定启用或禁用 ``path``，返回改名后的路径。

    任何不能确定的场景（扩展名不符、源文件不存在、目标已存在）都抛
    :class:`ModToggleError`，并且不会留下半完成状态。
    """
    target_path = Path(path)
    if enabled:
        if not is_disabled_path(target_path):
            raise ModToggleError(f"只有 .dll.disable 文件可以启用：{target_path.name}")
        new_path = enabled_path_for(target_path)
    else:
        if not is_loadable_path(target_path):
            raise ModToggleError(f"只有 .dll 文件可以禁用：{target_path.name}")
        new_path = disabled_path_for(target_path)

    if not (target_path.is_file() or target_path.is_symlink()):
        raise ModToggleError(f"文件不存在：{target_path.name}")
    if new_path.exists():
        raise ModToggleError(f"目标文件已存在：{new_path.name}")

    try:
        target_path.rename(new_path)
    except OSError as exc:
        raise ModToggleError(f"重命名失败：{exc}") from exc
    return new_path


def actual_path_for(path: Path) -> Path | None:
    """这个**逻辑文件**在磁盘上的真实路径：规范名优先，其次 `.dll.disable`；都没有返回 `None`。

    状态里一律只记规范路径（`X.dll`），而禁用是把它改名成 `X.dll.disable` —— 所以
    "这个文件在哪"只允许有一个答案，不要在每个调用点各写一遍。
    """
    target = Path(path)
    if target.is_file():
        return target
    disabled = target.with_name(target.name + DISABLE_SUFFIX)
    return disabled if disabled.is_file() else None


def actual_managed_path(game_dir: Path | str, relative: str) -> Path | None:
    """`<game_dir>/<relative>` 的真实路径（`relative` 用规范名写，例如 `Mods/X.dll`）。"""
    return actual_path_for(Path(game_dir).expanduser() / str(relative).replace("\\", "/"))


def apply_enabled(path: Path, enabled: bool) -> Path:
    """把文件切到目标状态并返回磁盘上的路径；**已经是目标状态就原样返回**（幂等）。

    调用方因此只需要给出基本名（`Mods/X.dll`）：文件当前是 `.dll` 还是 `.dll.disable` 由这里判断。
    """
    actual = actual_path_for(Path(path))
    if actual is None:
        raise ModToggleError(f"文件不存在：{Path(path).name}")
    if enabled == is_disabled_path(actual):
        return set_enabled(actual, enabled)
    return actual
