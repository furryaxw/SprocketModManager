from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Callable

from ..domain.errors import DownloadError, InstallError
from ..domain.models import (
    PreparedAsset,
    PreparedPackage,
    PreparedPlan,
    ProgressCallback,
    ReleaseAsset,
    ResolutionPlan,
)
from ..infrastructure.github import GitHubClient
from ..infrastructure.http_client import HttpClient
from ..infrastructure.release_checksums import publisher_checksum
from ..infrastructure.scanner import PackageScanner
from ..utilities.checksums import sha256_file
from ..utilities.package_paths import (
    validate_file_type,
    validate_relative_path,
    validate_supply_target,
)


class PlanPreparer:
    def __init__(self, app_dir: Path, http: HttpClient, github: GitHubClient):
        self.app_dir = app_dir
        self.http = http
        self.github = github

    @staticmethod
    def install_directories(plan: ResolutionPlan) -> dict[str, PurePosixPath]:
        """计划里的加载器供给表：类型 -> 游戏根目录下的相对目录。

        隐式依赖保证供给者一定在计划里，所以这里不需要再问注册表。同一个类型在计划里出现
        两个供给者时无法确定该用哪个目录，直接报错而不是让后一个悄悄覆盖前一个。
        """
        directories: dict[str, PurePosixPath] = {}
        owners: dict[str, str] = {}
        for resolved in plan.packages:
            for file_type, target in resolved.package.supply.items():
                validated = validate_file_type(file_type)
                owner = owners.get(validated)
                if owner is not None and owner != resolved.package.id:
                    raise InstallError(
                        f"install type {validated} is supplied by both {owner} and "
                        f"{resolved.package.id} in the same plan"
                    )
                owners[validated] = resolved.package.id
                directories[validated] = validate_supply_target(target)
        return directories

    def prepare(
            self,
            plan: ResolutionPlan,
            progress: ProgressCallback | None = None,
            private_downloaders: dict[str, Callable[[ReleaseAsset, Path, ProgressCallback | None], Path]] | None = None,
    ) -> PreparedPlan:
        work_root = self.app_dir / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="prepare-", dir=work_root))
        prepared_packages: list[PreparedPackage] = []
        directories = self.install_directories(plan)
        scanner = PackageScanner(directories)
        try:
            for resolved in plan.packages:
                package = resolved.package
                item = PreparedPackage(resolved=resolved)
                package_dir = work_dir / hashlib.sha256(package.id.encode("utf-8")).hexdigest()[:20]
                for asset in self.github.install_assets(package, resolved.release):
                    safe_name = validate_relative_path(asset.name)
                    if len(safe_name.parts) != 1:
                        raise DownloadError(f"Release asset name contains a path: {asset.name}")
                    if progress:
                        progress(f"Downloading {package.label()} {resolved.release.version}: {asset.name}")
                    destination = package_dir / "assets" / asset.name
                    downloader = (private_downloaders or {}).get(package.id)
                    if downloader is not None:
                        downloader(asset, destination, progress)
                    else:
                        self.http.download(
                            asset, destination, progress=None, hosts=set(package.asset_hosts())
                        )
                    actual_digest = sha256_file(destination)
                    expected = publisher_checksum(
                        self.http, package, resolved.release, asset
                    )
                    publisher_verified = expected is not None
                    expected_digest = expected[0] if expected else None
                    if expected_digest and actual_digest != expected_digest:
                        raise DownloadError(
                            f"SHA-256 mismatch for {asset.name}: expected {expected_digest}, got {actual_digest}"
                        )
                    item.assets.append(
                        PreparedAsset(
                            asset=asset,
                            path=destination,
                            sha256=actual_digest,
                            publisher_verified=publisher_verified,
                            publisher_digest=expected_digest,
                        )
                    )
                    files, ignored = scanner.scan(
                        package,
                        destination,
                        package_dir / "scan" / str(asset.id),
                    )
                    item.files.extend(files)
                    item.ignored_files.extend(ignored)
                if not item.files:
                    raise DownloadError(f"{package.id}: selected Release assets contain no installable files")
                prepared_packages.append(item)
            return PreparedPlan(plan, prepared_packages, work_dir, directories)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    @staticmethod
    def discard(prepared: PreparedPlan) -> None:
        shutil.rmtree(prepared.work_dir, ignore_errors=True)
