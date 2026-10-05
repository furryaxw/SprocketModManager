"""BepInEx 标识符：运行时布局的检测，加上 `BepInEx/plugins` 与 `BepInEx/patchers` 两类目录。

检测认 doorstop 代理（`winhttp.dll` 或 `doorstop_config.ini`）加 `BepInEx/core/BepInEx*.dll`。

`identify()` 与 MelonLoader 那条同一口径：目录由标识符拥有，身份来自程序集自己的静态元数据
（`Sprocket.Mod.Id` 等 AssemblyMetadata），没有声明身份的程序集沿用程序集名、没有 registry 匹配。
BepInEx 特有的只有插件类上的特性 —— `[BepInPlugin]` 给插件 GUID 与插件版本，
`[BepInDependency]` / `[BepInIncompatibility]` 给的是插件 GUID，本地依赖图按它匹配。
"""

from __future__ import annotations

from pathlib import Path

from . import (
    BEPINEX_CAPABILITY,
    BEPINEX_NAMESPACE,
    DetectedRuntime,
    LogSource,
    ModIdentifier,
    ModType,
    first_pe_file_version,
)
from ...infrastructure.dll_metadata import DllMetadata, read_cached_metadata


class BepInExIdentifier(ModIdentifier):
    namespace = BEPINEX_NAMESPACE
    capability = BEPINEX_CAPABILITY
    types = (
        ModType(
            id="bepinex:plugin",
            directory="BepInEx/plugins",
            kind="BepInEx plugins",
        ),
        ModType(
            id="bepinex:patchers",
            directory="BepInEx/patchers",
            kind="BepInEx patchers",
        ),
    )

    def detect(self, game_path: Path) -> DetectedRuntime | None:
        root = Path(game_path)
        proxy = (root / "winhttp.dll").is_file() or (root / "doorstop_config.ini").is_file()
        core = root / "BepInEx" / "core"
        candidates = sorted(core.glob("BepInEx*.dll")) if core.is_dir() else []
        if not proxy or not candidates:
            return None
        return DetectedRuntime(first_pe_file_version(candidates))

    def identify(self, path: Path) -> DllMetadata | None:
        return read_cached_metadata(path)

    def runtime_paths(self, detected: DetectedRuntime) -> tuple[str, ...]:
        return ("BepInEx",)

    def log_files(self, home: str = "") -> tuple[LogSource, ...]:
        return (LogSource(id="bepinex", loader="BepInEx", path="BepInEx/LogOutput.log"),)
