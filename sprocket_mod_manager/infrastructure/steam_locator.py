from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SPROCKET_STEAM_APP_ID = "1674170"
_VDF_PAIR = re.compile(r'^\s*"(?P<key>[^"]+)"\s+"(?P<value>(?:\\.|[^"])*)"', re.MULTILINE)


def _steam_install_roots() -> tuple[Path, ...]:
    candidates: list[Path] = []
    if os.name == "nt":
        try:
            import winreg

            registry_locations = (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", ("SteamPath", "InstallPath")),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", ("InstallPath",)),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", ("InstallPath",)),
            )
            for hive, key_name, value_names in registry_locations:
                try:
                    with winreg.OpenKey(hive, key_name) as key:
                        for value_name in value_names:
                            try:
                                value, _kind = winreg.QueryValueEx(key, value_name)
                            except OSError:
                                continue
                            if isinstance(value, str) and value.strip():
                                candidates.append(Path(value.strip()))
                except OSError:
                    continue
        except (ImportError, AttributeError):
            pass

    for environment_name in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        program_files = os.environ.get(environment_name)
        if program_files:
            candidates.append(Path(program_files) / "Steam")

    return _unique_paths(candidates)


def _unique_paths(paths: list[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        identity = os.path.normcase(os.path.abspath(path))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(path)
    return tuple(unique)


def _read_vdf_pairs(path: Path) -> tuple[tuple[str, str], ...]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ()
    pairs = []
    for match in _VDF_PAIR.finditer(text):
        value = re.sub(r'\\(["\\])', r'\1', match.group("value"))
        pairs.append((match.group("key"), value))
    return tuple(pairs)


def _steam_library_paths() -> tuple[Path, ...]:
    libraries: list[Path] = []
    for steam_root in _steam_install_roots():
        libraries.append(steam_root)
        library_file = steam_root / "steamapps" / "libraryfolders.vdf"
        libraries.extend(
            Path(value)
            for key, value in _read_vdf_pairs(library_file)
            if key.casefold() == "path" and value.strip()
        )
    return _unique_paths(libraries)


def _steam_game_path() -> Path | None:
    for library in _steam_library_paths():
        steamapps = library / "steamapps"
        manifest = steamapps / f"appmanifest_{SPROCKET_STEAM_APP_ID}.acf"
        install_dir = next(
            (
                value
                for key, value in _read_vdf_pairs(manifest)
                if key.casefold() == "installdir" and value.strip()
            ),
            "",
        )
        relative = Path(install_dir)
        if not install_dir or relative.is_absolute() or ".." in relative.parts:
            continue
        candidate = steamapps / "common" / relative
        if (candidate / "Sprocket.exe").is_file():
            return candidate.resolve()
    return None


def detect_game_path() -> str:
    steam_path = _steam_game_path()
    if steam_path:
        return str(steam_path)

    candidates = [Path.cwd()]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).resolve().parent)
    for candidate in candidates:
        if (candidate / "Sprocket.exe").is_file():
            return str(candidate.resolve())
    return ""
