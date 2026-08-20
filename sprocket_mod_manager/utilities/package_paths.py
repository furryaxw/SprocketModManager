from __future__ import annotations

from pathlib import PurePosixPath

from ..domain.errors import ScanError

STANDARD_ROOTS = {"Mods", "Plugins", "UserLibs", "UserData"}
ALLOWED_ROOTS = STANDARD_ROOTS | {"AutoTranslator"}
XUNITY_TRANSLATION_MODE = "xunity-translation"


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
