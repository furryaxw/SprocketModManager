from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .errors import DownloadError
from .models import RegistryPackage, ReleaseInfo
from .service import ModManagerService


def load_catalog(
    service: ModManagerService,
    source: str | Path,
    *,
    refresh: bool,
    on_registry_loaded: Callable[[ModManagerService], None] | None = None,
    on_release_loaded: Callable[[str, ReleaseInfo | None], None] | None = None,
) -> tuple[ModManagerService, dict[str, ReleaseInfo | None]]:
    registry = service.load_registry(source, refresh=refresh)
    if on_registry_loaded:
        on_registry_loaded(service)

    def load_latest(package: RegistryPackage) -> tuple[str, ReleaseInfo | None]:
        try:
            releases = service.github.releases(package, refresh=False)
        except DownloadError:
            return package.id, None
        usable = [
            release
            for release in releases
            if service.github.install_assets(package, release)
        ]
        return package.id, usable[0] if usable else None

    packages = tuple(registry.packages)
    if not packages:
        return service, {}

    latest: dict[str, ReleaseInfo | None] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(packages))) as executor:
        futures = [executor.submit(load_latest, package) for package in packages]
        for future in as_completed(futures):
            package_id, release = future.result()
            latest[package_id] = release
            if on_release_loaded:
                on_release_loaded(package_id, release)
    return service, latest
