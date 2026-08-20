from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .base import ApiController
from ..api_support import release_data as _release_data, source_from_config as _source_from_config
from ...application.catalog import load_catalog
from ...application.service import ModManagerService
from ...domain.errors import ModManagerError
from ...domain.models import ReleaseInfo
from ...infrastructure.config import effective_game_path, effective_index_url


class CatalogController(ApiController):
    def load_catalog(self, refresh: bool = False) -> dict[str, Any]:
        if not self._catalog_lock.acquire(blocking=False):
            return self._failure(RuntimeError("catalog load is already running"), code="catalog_busy")
        try:
            self.config = self.config_store.load()
            service = self._service_factory(self.config_store.app_dir)
            self._configure_service_network(service)
            service, latest = load_catalog(
                service,
                _source_from_config(self.config),
                refresh=bool(refresh),
            )
            adopted = self._adopt_existing(service)
            with self._state_lock:
                self.service = service
                self.latest = latest
            return self._success(
                packages=self._catalog_data(service, latest),
                installed=self._installed_data(service),
                unrecognized=self._unrecognized_mods(service),
                adopted=adopted,
                has_any_mods=self._has_any_mods(),
                source=effective_index_url(self.config),
            )
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="catalog_load_failed")
        finally:
            self._catalog_lock.release()

    def _catalog_data(
            self,
            service: ModManagerService,
            latest: dict[str, ReleaseInfo | None],
    ) -> list[dict[str, Any]]:
        registry = service.registry
        if registry is None:
            return []
        installed = self._installed(service)
        packages: list[dict[str, Any]] = []
        for package in registry.packages:
            release = latest.get(package.id)
            selected_assets = (
                service.github.install_assets(package, release)
                if release is not None
                else ()
            )
            packages.append(
                {
                    "id": package.id,
                    "name": package.name,
                    "display_name": dict(package.display_name),
                    "description": dict(package.description),
                    "authors": list(package.authors),
                    "repository": package.repository,
                    "repository_url": f"https://github.com/{package.repository}",
                    "license": package.license,
                    "category": package.category,
                    "tags": list(package.tags),
                    "dependencies": [dict(item) for item in package.dependencies],
                    "recommendations": list(package.recommendations),
                    "featured": package.featured,
                    "release": _release_data(release),
                    "install_assets": [asset.name for asset in selected_assets],
                    "installed": self._installed_entry(installed.get(package.id)),
                }
            )
        return packages

    @staticmethod
    def _installed_entry(info: dict[str, Any] | None) -> dict[str, Any] | None:
        if not info:
            return None
        return {
            "name": str(info.get("name", "")),
            "version": str(info.get("version", "")),
            "requested": bool(info.get("requested")),
            "adopted": bool(info.get("adopted")),
            "dependencies": list(info.get("dependencies", ())),
        }

    def _installed(self, service: ModManagerService | None = None) -> dict[str, dict[str, Any]]:
        value = effective_game_path(self.config)
        if not value:
            return {}
        path = Path(value).expanduser()
        if not (path / "Sprocket.exe").is_file():
            return {}
        return (service or self.service).installed(path)

    def _current_service(self) -> ModManagerService:
        with self._state_lock:
            return self.service

    def _installed_data(self, service: ModManagerService | None = None) -> list[dict[str, Any]]:
        installed = self._installed(service)
        return [
            {"id": package_id, **(self._installed_entry(info) or {})}
            for package_id, info in sorted(installed.items())
        ]

    def get_installed(self) -> dict[str, Any]:
        try:
            service = self._current_service()
            adopted = self._adopt_existing(service)
            return self._success(
                installed=self._installed_data(service),
                unrecognized=self._unrecognized_mods(service),
                adopted=adopted,
                has_any_mods=self._has_any_mods(),
            )
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="installed_load_failed")

    def _adopt_existing(self, service: ModManagerService) -> list[dict[str, Any]]:
        value = effective_game_path(self.config)
        if not value:
            return []
        game_path = Path(value).expanduser()
        if not (game_path / "Sprocket.exe").is_file():
            return []
        if not self._mutation_lock.acquire(blocking=False):
            return []
        try:
            return [
                {
                    "id": record.package_id,
                    "name": record.name,
                    "version": record.version,
                    "files": list(record.files),
                }
                for record in service.adopt_existing(game_path)
            ]
        finally:
            self._mutation_lock.release()

    def _has_any_mods(self) -> bool:
        value = effective_game_path(self.config)
        if not value:
            return False
        game_path = Path(value).expanduser()
        if not (game_path / "Sprocket.exe").is_file():
            return False
        mods_dir = game_path / "Mods"
        if not mods_dir.is_dir():
            return False
        try:
            for path in mods_dir.rglob("*"):
                try:
                    if path.suffix.casefold() == ".dll" and (
                            path.is_file() or path.is_symlink()
                    ):
                        return True
                except OSError:
                    continue
        except OSError:
            return False
        return False

    def _unrecognized_mods(self, service: ModManagerService) -> list[dict[str, str]]:
        value = effective_game_path(self.config)
        if not value:
            return []
        game_path = Path(value).expanduser()
        if not (game_path / "Sprocket.exe").is_file():
            return []
        managed_paths = {
            relative.replace("\\", "/").casefold()
            for package in self._installed(service).values()
            for relative in package.get("files", ())
            if isinstance(relative, str)
        }
        unrecognized: list[dict[str, str]] = []
        for root_name in ("Mods", "UserLibs"):
            root = game_path / root_name
            if not root.is_dir():
                continue
            try:
                for path in root.rglob("*"):
                    try:
                        if path.suffix.casefold() != ".dll" or not (
                                path.is_file() or path.is_symlink()
                        ):
                            continue
                        relative = path.relative_to(game_path).as_posix()
                    except (OSError, ValueError):
                        continue
                    if relative.casefold() in managed_paths:
                        continue
                    unrecognized.append({"name": path.name, "path": relative})
            except OSError:
                continue
        return sorted(unrecognized, key=lambda item: item["path"].casefold())

    def get_package_readme(self, package_id: str, refresh: bool = False) -> dict[str, Any]:
        try:
            service = self._current_service()
            package = self._package(service, str(package_id))
            readme = service.github.repository_readme(
                package.repository,
                refresh=bool(refresh),
            )
            return self._success(
                package_id=package.id,
                html=readme.html,
                page_url=readme.page_url,
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc, code="readme_load_failed")

    def open_readme_link(self, package_id: str, url: str) -> dict[str, Any]:
        try:
            self._package(self._current_service(), str(package_id))
            parsed = urlparse(str(url))
            if parsed.scheme != "https" or not parsed.hostname or len(str(url)) > 4096:
                raise ValueError("README links must use HTTPS")
            webbrowser.open(str(url))
            return self._success()
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc, code="open_url_failed")
