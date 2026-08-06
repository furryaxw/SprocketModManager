from __future__ import annotations

import json
import locale
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .service import DEFAULT_INDEX_URL, default_app_dir


SPROCKET_STEAM_APP_ID = "1674170"
DEFAULT_TEXT_SCALE = 100
MIN_TEXT_SCALE = 100
MAX_TEXT_SCALE = 160
DEFAULT_PROXY_URL = "http://127.0.0.1:7890"
DEFAULT_GITHUB_PROXY_URL = "https://gh-proxy.com/"
_VDF_PAIR = re.compile(r'^\s*"(?P<key>[^"]+)"\s+"(?P<value>(?:\\.|[^"])*)"', re.MULTILINE)


def language_from_locale_name(value: str | None) -> str:
    normalized = (value or "").casefold().replace("_", "-")
    return "zh" if normalized.startswith("zh") or "chinese" in normalized else "en"


def _windows_user_locale() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
            return buffer.value
    except (AttributeError, OSError, ValueError):
        pass
    return ""


def detect_language() -> str:
    names: list[str] = []
    try:
        names.append(locale.getlocale()[0] or "")
    except (ValueError, TypeError):
        pass
    windows_locale = _windows_user_locale()
    if windows_locale:
        names.append(windows_locale)
    return "zh" if any(language_from_locale_name(name) == "zh" for name in names) else "en"


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


def _configured_text(config: dict[str, Any], key: str) -> str:
    value = config.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def effective_game_path(config: dict[str, Any]) -> str:
    return _configured_text(config, "game_path") or detect_game_path()


def effective_index_url(config: dict[str, Any]) -> str:
    return _configured_text(config, "index_url") or DEFAULT_INDEX_URL


def normalize_proxy_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("proxy URL must be an HTTP(S) server URL")
    return text.rstrip("/")


def normalize_github_proxy_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("GitHub proxy URL must use HTTPS")
    return text.rstrip("/") + "/"


def effective_proxy_url(config: dict[str, Any]) -> str:
    if config.get("proxy_enabled") is not True:
        return ""
    return normalize_proxy_url(config.get("proxy_url", "")) or DEFAULT_PROXY_URL


def effective_github_proxy_url(config: dict[str, Any]) -> str:
    if config.get("github_proxy_enabled") is not True:
        return ""
    return (
        normalize_github_proxy_url(config.get("github_proxy_url", ""))
        or DEFAULT_GITHUB_PROXY_URL
    )


def normalize_text_scale(value: object) -> int:
    if isinstance(value, bool):
        return DEFAULT_TEXT_SCALE
    try:
        scale = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TEXT_SCALE
    return max(MIN_TEXT_SCALE, min(MAX_TEXT_SCALE, scale))


class ConfigStore:
    def __init__(self, app_dir: Path | None = None):
        self.app_dir = app_dir or default_app_dir()
        self.path = self.app_dir / "config.json"

    def load(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "language": "auto",
            "game_path": "",
            "index_url": "",
            "proxy_enabled": False,
            "proxy_url": "",
            "github_proxy_enabled": False,
            "github_proxy_url": "",
            "text_scale": DEFAULT_TEXT_SCALE,
        }
        if not self.path.is_file():
            return defaults
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return defaults
        if isinstance(data, dict):
            defaults.update({key: value for key, value in data.items() if isinstance(key, str)})
        defaults["text_scale"] = normalize_text_scale(defaults["text_scale"])
        return defaults

    def save(self, config: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
