"""BepInEx 标识符的壳：检测运行时、声明目录，但不认任何身份。

检测认 doorstop 代理（`winhttp.dll` 或 `doorstop_config.ini`）加
`BepInEx/core/BepInEx*.dll`。没有受支持的 BepInEx 模组，所以 `identify()` 一律返回
`None`：`BepInEx/plugins` 与 `BepInEx/patchers` 里的文件只会作为本地未知条目出现
（有文件名和路径，没有名字、版本、作者或声明 ID），也不参与启用/禁用——那要等这个标识符
真正认识它们的形式。
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
from ...infrastructure.dll_metadata import DllMetadata


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
        return None

    def runtime_paths(self, detected: DetectedRuntime) -> tuple[str, ...]:
        return ("BepInEx",)

    def log_files(self, home: str = "") -> tuple[LogSource, ...]:
        return (LogSource(id="bepinex", loader="BepInEx", path="BepInEx/LogOutput.log"),)
