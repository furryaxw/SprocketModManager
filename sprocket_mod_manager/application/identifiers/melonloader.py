"""MelonLoader 标识符：运行时布局的检测，加上 `Mods` / `Plugins` / `UserLibs` 三类目录。

检测认两种布局：游戏根目录的 `version.dll` 代理加 `MelonLoader/net*/MelonLoader.dll`
（版本从那个 DLL 的 PE 版本信息读），以及桥接加载器把同一套运行时安家到的 `MLLoader/`。

`identify()` 就是那次静态 PE 读取；MelonLoader 加载它目录里的托管程序集，不是 PE 文件时
`read_cached_metadata` 抛 `ScanError`，由扫描方降级成 `error` 字段。
"""

from __future__ import annotations

from pathlib import Path

from . import (
    MELONLOADER_CAPABILITY,
    MELONLOADER_NAMESPACE,
    DetectedRuntime,
    LogSource,
    ModIdentifier,
    ModType,
    first_pe_file_version,
)
from ...infrastructure.dll_metadata import (
    MELON_KIND_MODS,
    MELON_KIND_PLUGINS,
    DllMetadata,
    read_cached_metadata,
)

USERLIB_KIND = "UserLibs"


class MelonLoaderIdentifier(ModIdentifier):
    namespace = MELONLOADER_NAMESPACE
    capability = MELONLOADER_CAPABILITY
    types = (
        ModType(id="melonloader:mod", directory="Mods", kind=MELON_KIND_MODS, toggleable=True),
        ModType(id="melonloader:plugin", directory="Plugins", kind=MELON_KIND_PLUGINS, toggleable=True),
        # 用户库是被别的模组引用的库，就地改名会连累依赖者，所以不参与启用/禁用。
        ModType(id="melonloader:userlib", directory="UserLibs", kind=USERLIB_KIND, toggleable=False),
    )

    def detect(self, game_path: Path) -> DetectedRuntime | None:
        root = Path(game_path)
        native = root / "MelonLoader"
        if (root / "version.dll").is_file() and native.is_dir():
            candidates = sorted(native.glob("net*/MelonLoader.dll"))
            if candidates:
                return DetectedRuntime(first_pe_file_version(candidates))
        # 桥接加载器把运行时安家到 `MLLoader/` 下，它提供的还是同一个能力。
        bridged = root / "MLLoader"
        if bridged.is_dir():
            candidates = sorted((bridged / "MelonLoader").glob("net*/MelonLoader.dll"))
            return DetectedRuntime(first_pe_file_version(candidates), home="MLLoader")
        return None

    def identify(self, path: Path) -> DllMetadata | None:
        return read_cached_metadata(path)

    def runtime_paths(self, detected: DetectedRuntime) -> tuple[str, ...]:
        return (f"{detected.home}/MelonLoader" if detected.home else "MelonLoader",)

    def log_files(self, home: str = "") -> tuple[LogSource, ...]:
        base = f"{home}/MelonLoader" if home else "MelonLoader"
        return (LogSource(id="melonloader", loader="MelonLoader", path=f"{base}/Latest.log"),)
