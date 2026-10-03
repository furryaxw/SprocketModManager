from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .service import ModManagerService
from ..domain.errors import DownloadError, RegistryError
from ..domain.models import RegistryPackage, ReleaseInfo

LOGGER = logging.getLogger(__name__)


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
        """这个包能装的最新一版；这一包自己读不出来就当作没有版本，不牵连别的包。"""
        try:
            releases = service.github.releases(package, refresh=False)
            usable = [
                release
                for release in releases
                if service.github.install_assets(package, release)
            ]
        except (DownloadError, KeyError, RegistryError, ValueError) as exc:
            LOGGER.warning("release lookup failed package=%s error=%s", package.id, exc)
            return package.id, None
        return package.id, usable[0] if usable else None

    packages = tuple(registry.packages)
    if not packages:
        return service, {}

    latest: dict[str, ReleaseInfo | None] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(packages))) as executor:
        futures = [executor.submit(load_latest, package) for package in packages]
        for future in as_completed(futures):
            try:
                package_id, release = future.result()
            except Exception as exc:  # 单包的任何毛病都不许废掉整份目录读数
                LOGGER.warning("release lookup crashed error=%s", exc)
                continue
            latest[package_id] = release
            if on_release_loaded:
                on_release_loaded(package_id, release)
    return service, latest
