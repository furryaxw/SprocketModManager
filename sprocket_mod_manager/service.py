from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import RegistryError
from .github import GitHubClient, HttpClient, is_loopback_host
from .installer import Installer
from .models import PreparedPlan, ProgressCallback, ResolutionPlan
from .preparer import PlanPreparer
from .registry import Registry
from .solver import DependencySolver
from .state import StateStore


DEFAULT_INDEX_URL = "https://sprocketmods.furryaxw.top/index.json"


def default_app_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "SprocketModManager" if local else Path.home() / ".sprocket-mod-manager"


class ModManagerService:
    def __init__(self, app_dir: Path | None = None, token: str | None = None):
        self.app_dir = app_dir or default_app_dir()
        self.http = HttpClient(self.app_dir / "cache", token=token)
        self.github = GitHubClient(self.http)
        self.registry: Registry | None = None

    def _installer_for(self, game_dir: Path) -> Installer:
        normalized = str(game_dir.expanduser().resolve()).casefold()
        profile = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
        profile_dir = self.app_dir / "profiles" / profile
        profile_dir.mkdir(parents=True, exist_ok=True)
        marker = profile_dir / "game-path.txt"
        if not marker.is_file():
            marker.write_text(str(game_dir.expanduser().resolve()), encoding="utf-8")
        return Installer(self.app_dir, StateStore(profile_dir / "installed.json"))

    def load_registry(self, source: str | Path, *, refresh: bool = False) -> Registry:
        if isinstance(source, Path) or (isinstance(source, str) and not urlparse(source).scheme):
            registry = Registry.from_file(Path(source))
        else:
            url = str(source)
            parsed = urlparse(url)
            is_loopback_http = parsed.scheme == "http" and is_loopback_host(parsed.hostname)
            if parsed.scheme != "https" and not is_loopback_http:
                raise RegistryError("registry URL must use HTTPS or loopback HTTP")
            data = self.http.get_bytes(
                url,
                accept="application/json",
                max_bytes=16 * 1024 * 1024,
                cache_seconds=0 if refresh else 300,
                allow_loopback_http=True,
            )
            try:
                registry = Registry.from_dict(json.loads(data.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RegistryError(f"invalid registry JSON: {exc}") from exc
        self.registry = registry
        return registry

    def _require_registry(self) -> Registry:
        if not self.registry:
            raise RegistryError("registry is not loaded")
        return self.registry

    def resolve(self, identifier: str, version_range: str = "*") -> ResolutionPlan:
        registry = self._require_registry()
        package = registry.resolve_identifier(identifier)
        return DependencySolver(registry, self.github).resolve(package.id, version_range)

    def prepare(
        self,
        plan: ResolutionPlan,
        progress: ProgressCallback | None = None,
    ) -> PreparedPlan:
        return PlanPreparer(self.app_dir, self.http, self.github).prepare(plan, progress)

    def install(
        self,
        identifier: str,
        game_dir: Path,
        *,
        version_range: str = "*",
        progress: ProgressCallback | None = None,
    ) -> tuple[ResolutionPlan, list[str]]:
        plan = self.resolve(identifier, version_range)
        prepared = self.prepare(plan, progress)
        try:
            warnings = self._installer_for(game_dir).apply(prepared, game_dir, progress=progress)
            return plan, warnings
        finally:
            PlanPreparer.discard(prepared)

    def remove(self, identifier: str, game_dir: Path) -> tuple[list[str], list[str]]:
        registry = self._require_registry()
        package = registry.resolve_identifier(identifier)
        return self._installer_for(game_dir).remove(package.id, game_dir)

    def installed(self, game_dir: Path) -> dict[str, dict[str, Any]]:
        return self._installer_for(game_dir).state_store.load()["packages"]
