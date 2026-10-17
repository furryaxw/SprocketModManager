from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Sequence

from .releases import installable_release
from .service import ModManagerService
from ..domain.errors import DownloadError, RegistryError
from ..domain.models import RegistryPackage, ReleaseInfo
from ..infrastructure.github import GitHubClient
from ..infrastructure.registry_source import DOCUMENT_NAMES

LOGGER = logging.getLogger(__name__)


def latest_release_for(github: GitHubClient, package: RegistryPackage) -> ReleaseInfo | None:
    """这个包能装的最新一版；一版都装不了就是 None。

    「能装」的判据只有一处（`installable_release`）：普通包看有没有可安装资产，整合包还认条目里
    带来的成员与落点规则。目录读数与服务器那份读数都问这一个函数。
    """
    releases = github.releases(package, refresh=False)
    usable = [item for item in releases if installable_release(package, item, github)]
    return usable[0] if usable else None


def load_catalog(
        service: ModManagerService,
        source: str | Path,
        *,
        refresh: bool,
        documents: Sequence[str] = DOCUMENT_NAMES,
        on_registry_loaded: Callable[[ModManagerService], None] | None = None,
        on_release_loaded: Callable[[str, ReleaseInfo | None], None] | None = None,
) -> tuple[ModManagerService, dict[str, ReleaseInfo | None]]:
    registry = service.load_registry(source, refresh=refresh, documents=documents)
    if on_registry_loaded:
        on_registry_loaded(service)

    def load_latest(package: RegistryPackage) -> tuple[str, ReleaseInfo | None]:
        """这个包能装的最新一版；这一包自己读不出来就当作没有版本，不牵连别的包。"""
        try:
            release = latest_release_for(service.github, package)
        except (DownloadError, KeyError, RegistryError, ValueError) as exc:
            LOGGER.warning("release lookup failed package=%s error=%s", package.id, exc)
            return package.id, None
        return package.id, release

    # 只算目录读数会列出的那些：索引之外来的包由它们自己那份读数去算。
    packages = tuple(registry.index_packages)
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
