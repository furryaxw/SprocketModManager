"""模组标识符：按运行时拆开「本地模组住在哪个目录、怎么认身份」，并按当前环境激活。

一个标识符拥有一个**命名空间**（供给类型 `melonloader:mod` 里的 `melonloader`）和一条
**能力**（`lavagang.melonloader` / `bepinex.bepinex`）。它读哪些目录由**已安装的**同命名
空间供给者的 `supply` 表给出，所以桥接加载器把模组安家到 `MLLoader/Mods` 时扫描跟着走，
不需要在别处再写一份目录名。

标识符同时负责**检测自己的运行时**：能检测到就算在场，检测到的版本直接喂给环境的能力表。
因此标识符激活的条件是「声明的能力在场」——已装供给者、环境里的能力、或磁盘上检测到运行时，
三者之一。都不成立时这个标识符不读任何目录。

`identify()` 只回答一个问题：这个文件是不是我的；是就给出静态元数据。返回 `None` 表示
不是——调用方把文件当本地未知条目，不替它编名字、版本或身份。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from ...domain.models import RegistryPackage
from ...infrastructure.dll_metadata import DllMetadata, pe_file_version
from ...utilities.package_paths import file_type_namespace, validate_supply_target

MELONLOADER_NAMESPACE = "melonloader"
MELONLOADER_CAPABILITY = "lavagang.melonloader"
BEPINEX_NAMESPACE = "bepinex"
BEPINEX_CAPABILITY = "bepinex.bepinex"


@dataclass(frozen=True)
class ModType:
    """一个标识符认识的一种模组类型。

    `id` 是供给表里的类型（`melonloader:mod`）；`directory` 是没有已装供给者时的目录；
    `kind` 是本地清单里显示的类别；`toggleable` 决定这个目录里的文件能不能启用/禁用。
    """

    id: str
    directory: str
    kind: str
    toggleable: bool


@dataclass(frozen=True)
class ModDirectory:
    """扫描时用到的一个目录：相对游戏根目录的路径，加上它承载的类型。"""

    path: str
    type: ModType


@dataclass(frozen=True)
class LogSource:
    """运行时写的一份日志：稳定的 `id`、界面用的运行时可读名、相对游戏根目录的路径。

    路径按检测到的 `home` 拼（桥接布局的运行时不在游戏根目录）；文件在不在由调用方判断。
    """

    id: str
    loader: str
    path: str


@dataclass(frozen=True)
class DetectedRuntime:
    """磁盘上真的存在这个运行时；版本读不出来时是空串。

    `home` 是运行时所在的相对目录（`""` 表示游戏根目录，`MLLoader` 表示桥接加载器把
    运行时安家的位置）：没有已装供给者时，标识符声明的目录相对它。
    """

    version: str = ""
    home: str = ""


def first_pe_file_version(candidates: Iterable[Path]) -> str:
    """候选文件里第一个读得出版本的 PE 版本；一个都读不出来返回空串。"""
    for candidate in candidates:
        version = pe_file_version(candidate)
        if version:
            return version
    return ""


class ModIdentifier:
    """标识符契约：命名空间、能力、认识的类型、运行时检测与身份读取。"""

    namespace: str = ""
    capability: str = ""
    types: tuple[ModType, ...] = ()

    def type_for(self, file_type: str) -> ModType | None:
        for mod_type in self.types:
            if mod_type.id == file_type:
                return mod_type
        return None

    def detect(self, game_path: Path) -> DetectedRuntime | None:
        """磁盘上是否存在这个运行时（不看安装记录）；存在就给出它的版本。"""
        raise NotImplementedError

    def directories(
            self,
            packages: Iterable[RegistryPackage],
            installed_ids: Iterable[str],
            home: str = "",
    ) -> tuple[ModDirectory, ...]:
        """现在要读的目录，来自同命名空间**已安装**供给者的 `supply` 表。

        供给位置按注册表里的目标解析（`{Sprocket}/MLLoader/Mods` -> `MLLoader/Mods`）。
        它不认识的类型（运行时的 `*:core` 之类）不读：那不是模组目录。一个供给者都没装时
        退回 `types` 里声明的目录，并挂到 `home` 下（检测到桥接布局时运行时不在游戏根目录）。
        """
        installed = {str(item) for item in installed_ids}
        found: list[ModDirectory] = []
        for package in packages:
            if package.id not in installed:
                continue
            for file_type, target in package.supply.items():
                if file_type_namespace(file_type) != self.namespace:
                    continue
                mod_type = self.type_for(file_type)
                if mod_type is None:
                    continue
                path = validate_supply_target(target).as_posix()
                if any(entry.path == path for entry in found):
                    continue
                found.append(ModDirectory(path, mod_type))
        if found:
            return tuple(found)
        return tuple(
            ModDirectory(f"{home}/{mod_type.directory}" if home else mod_type.directory, mod_type)
            for mod_type in self.types
            if mod_type.directory
        )

    def identify(self, path: Path) -> DllMetadata | None:
        """读取这个文件的身份；`None` 表示「不是我的」，即不要为它编造身份。"""
        raise NotImplementedError

    def runtime_paths(self, detected: DetectedRuntime) -> tuple[str, ...]:
        """检测到的运行时在游戏根目录里的顶层条目（相对路径）。

        这些是交还运行时时要整树删掉的目录；空表示这个运行时没有自己的树。
        """
        return ()

    def log_files(self, home: str = "") -> tuple[LogSource, ...]:
        """这个运行时写的日志；`home` 与 `directories()` 的取值一致。"""
        return ()


def _supplies_namespace(package: RegistryPackage, namespace: str) -> bool:
    return any(file_type_namespace(file_type) == namespace for file_type in package.supply)


def _present_capabilities(
        capabilities: Mapping[str, object] | Iterable[str],
) -> set[str]:
    """能力表（id -> 版本）或 id 列表都归一成 id 集合。"""
    return {str(item) for item in capabilities}


def detected_capabilities(game_path: Path | None) -> dict[str, str]:
    """磁盘上检测到的运行时：能力 id -> 检测到的版本。

    检测不看安装记录：加载器可能是管理器之外装上去的，记录里没有它。版本读不出来时**不进表**，
    而不是记一个空版本——空版本会被区间判定读成「不满足任何范围」，那比「版本未知」错得更远。
    """
    if game_path is None:
        return {}
    found: dict[str, str] = {}
    for identifier in IDENTIFIERS:
        detected = identifier.detect(game_path)
        if detected is not None and identifier.capability and detected.version:
            found[identifier.capability] = detected.version
    return found


def runtime_states(
        packages: Iterable[RegistryPackage],
        installed: Mapping[str, Mapping[str, object]],
        detected: Mapping[str, str],
) -> dict[str, tuple[bool, str]]:
    """每个加载器包的在场面貌：`id -> (在不在场, 是哪版)`。

    在场 = 安装记录里有它（`installed` 的键），或磁盘上检测到它的运行时——检测结果按能力 id
    给出，与运行时包的 id 同名。检测只在没有别的**记录在案**的包供给同一个能力时才算数：
    桥接加载器供给的也是同一个能力，它记录在案时磁盘上那份运行时就是它的，不另算一份。
    版本以记录为准；记录里没有版本才用检测到的那个（读不出版本的检测结果不进表）。
    """
    candidates = tuple(packages)
    recorded = {str(package_id): info for package_id, info in installed.items()}
    covered = {
        capability_id
        for package in candidates
        if package.id in recorded
        for capability_id in package.capabilities()
    }
    states: dict[str, tuple[bool, str]] = {}
    for package in candidates:
        info = recorded.get(package.id) or {}
        recorded_version = str(info.get("version") or "")
        detected_version = str(detected.get(package.id) or "")
        present = package.id in recorded or (
            bool(detected_version) and package.id not in covered
        )
        states[package.id] = (present, (recorded_version or detected_version) if present else "")
    return states


def is_active(
        identifier: ModIdentifier,
        packages: Iterable[RegistryPackage],
        installed_ids: Iterable[str],
        capabilities: Mapping[str, object] | Iterable[str] = (),
        detected: DetectedRuntime | None = None,
) -> bool:
    """标识符声明的能力是否在场：环境里的能力、已装的同命名空间供给者，或检测到的运行时。"""
    if identifier.capability and identifier.capability in _present_capabilities(capabilities):
        return True
    installed = {str(item) for item in installed_ids}
    if any(
        package.id in installed and _supplies_namespace(package, identifier.namespace)
        for package in packages
    ):
        return True
    return detected is not None


def _activation(
        identifier: ModIdentifier,
        game_path: Path | None,
        packages: Iterable[RegistryPackage],
        installed_ids: Iterable[str],
        capabilities: Mapping[str, object] | Iterable[str],
) -> tuple[bool, DetectedRuntime | None]:
    detected = identifier.detect(game_path) if game_path is not None else None
    return (
        is_active(identifier, packages, installed_ids, capabilities, detected),
        detected,
    )


def active_identifiers(
        game_path: Path | None,
        packages: Iterable[RegistryPackage],
        installed_ids: Iterable[str] = (),
        capabilities: Mapping[str, object] | Iterable[str] = (),
) -> tuple[ModIdentifier, ...]:
    return tuple(
        identifier
        for identifier in IDENTIFIERS
        if _activation(identifier, game_path, packages, installed_ids, capabilities)[0]
    )


def scan_targets(
        game_path: Path | None,
        packages: Iterable[RegistryPackage],
        installed_ids: Iterable[str] = (),
        capabilities: Mapping[str, object] | Iterable[str] = (),
) -> tuple[tuple[ModIdentifier, ModDirectory], ...]:
    """`(标识符, 目录)` 对：扫描用它同时知道目录和该由谁认身份；同一路径只留一次。"""
    found: list[tuple[ModIdentifier, ModDirectory]] = []
    seen: set[str] = set()
    for identifier in IDENTIFIERS:
        active, detected = _activation(identifier, game_path, packages, installed_ids, capabilities)
        if not active:
            continue
        home = detected.home if detected is not None else ""
        for directory in identifier.directories(packages, installed_ids, home):
            key = directory.path.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append((identifier, directory))
    return tuple(found)


def mod_directory_paths(
        game_path: Path | None,
        packages: Iterable[RegistryPackage],
        installed_ids: Iterable[str] = (),
        capabilities: Mapping[str, object] | Iterable[str] = (),
) -> tuple[str, ...]:
    return tuple(
        directory.path
        for _identifier, directory in scan_targets(game_path, packages, installed_ids, capabilities)
    )


def log_sources(
        game_path: Path | None,
        packages: Iterable[RegistryPackage],
        installed_ids: Iterable[str] = (),
        capabilities: Mapping[str, object] | Iterable[str] = (),
) -> tuple[LogSource, ...]:
    """活跃标识符声明的日志；同一个 `id` 只留一次。"""
    found: list[LogSource] = []
    seen: set[str] = set()
    for identifier in IDENTIFIERS:
        active, detected = _activation(identifier, game_path, packages, installed_ids, capabilities)
        if not active:
            continue
        home = detected.home if detected is not None else ""
        for source in identifier.log_files(home):
            key = source.id.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(source)
    return tuple(found)


def toggle_directories(
        game_path: Path | None,
        packages: Iterable[RegistryPackage],
        installed_ids: Iterable[str] = (),
        capabilities: Mapping[str, object] | Iterable[str] = (),
) -> tuple[str, ...]:
    """可启用/禁用的目录：标识符声明为可切换的类型所在的那些。"""
    return tuple(
        directory.path
        for _identifier, directory in scan_targets(game_path, packages, installed_ids, capabilities)
        if directory.type.toggleable
    )


# 已注册的标识符。列表顺序就是本地清单的分组顺序。
from .melonloader import MelonLoaderIdentifier  # noqa: E402  （契约先定义，具体标识符在下面注册）
from .bepinex import BepInExIdentifier  # noqa: E402

IDENTIFIERS: tuple[ModIdentifier, ...] = (MelonLoaderIdentifier(), BepInExIdentifier())

__all__ = [
    "BEPINEX_CAPABILITY",
    "BEPINEX_NAMESPACE",
    "DetectedRuntime",
    "IDENTIFIERS",
    "LogSource",
    "MELONLOADER_CAPABILITY",
    "MELONLOADER_NAMESPACE",
    "ModDirectory",
    "ModIdentifier",
    "ModType",
    "active_identifiers",
    "detected_capabilities",
    "first_pe_file_version",
    "is_active",
    "log_sources",
    "mod_directory_paths",
    "runtime_states",
    "scan_targets",
    "toggle_directories",
]
