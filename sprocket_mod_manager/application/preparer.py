from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from ..domain.errors import DownloadError
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
from ..utilities.package_paths import validate_relative_path


class PlanPreparer:
    def __init__(self, app_dir: Path, http: HttpClient, github: GitHubClient):
        self.app_dir = app_dir
        self.http = http
        self.github = github
        self.scanner = PackageScanner()

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
                        self.http.download(asset, destination, progress=None)
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
                    files, ignored = self.scanner.scan(
                        package,
                        destination,
                        package_dir / "scan" / str(asset.id),
                    )
                    item.files.extend(files)
                    item.ignored_files.extend(ignored)
                if not item.files:
                    raise DownloadError(f"{package.id}: selected Release assets contain no installable files")
                prepared_packages.append(item)
            return PreparedPlan(plan, prepared_packages, work_dir)
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

    @staticmethod
    def discard(prepared: PreparedPlan) -> None:
        shutil.rmtree(prepared.work_dir, ignore_errors=True)
