from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .adoption import AdoptionRecord, ExistingModsAdopter
from .preparer import PlanPreparer
from .solver import DependencySolver
from ..domain.errors import RegistryError
from ..domain.models import PreparedPlan, ProgressCallback, ResolutionPlan
from ..domain.registry import Registry
from ..infrastructure.defaults import DEFAULT_INDEX_URL, default_app_dir
from ..infrastructure.github import GitHubClient, HttpClient
from ..infrastructure.installer import Installer
from ..infrastructure.profiles import InstallerProfiles
from ..infrastructure.registry_source import RegistrySourceLoader

LOGGER = logging.getLogger(__name__)


class ModManagerService:
    def __init__(self, app_dir: Path | None = None, token: str | None = None):
        self.app_dir = app_dir or default_app_dir()
        LOGGER.debug("initializing service app_dir=%s token_configured=%s", self.app_dir, bool(token))
        self.http = HttpClient(self.app_dir / "cache", token=token)
        self.github = GitHubClient(self.http)
        self._registry_loader = RegistrySourceLoader(self.http)
        self._profiles = InstallerProfiles(self.app_dir)
        self.registry: Registry | None = None

    def _installer_for(self, game_dir: Path) -> Installer:
        return self._profiles.installer_for(game_dir)

    def load_registry(self, source: str | Path, *, refresh: bool = False) -> Registry:
        LOGGER.info("loading registry source=%s refresh=%s", source, refresh)
        registry = self._registry_loader.load(source, refresh=refresh)
        self.registry = registry
        LOGGER.info("registry loaded packages=%d", len(registry.packages))
        return registry

    def _require_registry(self) -> Registry:
        if not self.registry:
            raise RegistryError("registry is not loaded")
        return self.registry

    def resolve(self, identifier: str, version_range: str = "*") -> ResolutionPlan:
        LOGGER.debug("resolving package identifier=%s range=%s", identifier, version_range)
        registry = self._require_registry()
        package = registry.resolve_identifier(identifier)
        plan = DependencySolver(registry, self.github).resolve(package.id, version_range)
        LOGGER.info("resolved package=%s packages=%d", package.id, len(plan.packages))
        return plan

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
            force_conflicts: bool = False,
    ) -> tuple[ResolutionPlan, list[str]]:
        LOGGER.info("install requested identifier=%s game_dir=%s", identifier, game_dir)
        plan = self.resolve(identifier, version_range)
        prepared = self.prepare(plan, progress)
        try:
            warnings = self._installer_for(game_dir).apply(
                prepared,
                game_dir,
                progress=progress,
                force_conflicts=force_conflicts,
            )
            LOGGER.info("install completed identifier=%s warnings=%d", identifier, len(warnings))
            return plan, warnings
        finally:
            PlanPreparer.discard(prepared)

    def remove(self, identifier: str, game_dir: Path) -> tuple[list[str], list[str]]:
        LOGGER.info("remove requested identifier=%s game_dir=%s", identifier, game_dir)
        registry = self._require_registry()
        package = registry.resolve_identifier(identifier)
        result = self._installer_for(game_dir).remove(package.id, game_dir)
        LOGGER.info("remove completed package=%s removed=%d warnings=%d", package.id, len(result[0]), len(result[1]))
        return result

    def installed(self, game_dir: Path) -> dict[str, dict[str, Any]]:
        return self._installer_for(game_dir).state_store.load()["packages"]

    def adopt_existing(self, game_dir: Path) -> tuple[AdoptionRecord, ...]:
        registry = self._require_registry()
        installer = self._installer_for(game_dir)
        return ExistingModsAdopter(self.github, installer).adopt(registry, game_dir)
