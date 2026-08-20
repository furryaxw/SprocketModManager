from __future__ import annotations

import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from ..domain.errors import DownloadError
from ..domain.models import (
    PreparedAsset,
    PreparedPackage,
    PreparedPlan,
    ProgressCallback,
    ResolvedPackage,
    ResolutionPlan,
)
from ..infrastructure.private_servers import DeveloperServerClient, PrivatePackageManifest
from ..infrastructure.scanner import PackageScanner
from ..utilities.checksums import sha256_file


def prepare_private_package(
        app_dir: Path,
        client: DeveloperServerClient,
        package_id: str,
        manifest: PrivatePackageManifest,
        github_user_id: str,
        progress: ProgressCallback | None = None,
) -> PreparedPlan:
    archive = manifest.archive
    release = manifest.release
    if archive is None or release is None or not release.assets:
        raise DownloadError("private package has no downloadable release asset")

    expected: dict[str, str] = {}
    for file in manifest.files:
        folded = file.target.casefold()
        expected[folded] = file.sha256

    package = replace(manifest.package, id=package_id)
    asset = release.assets[0]
    resolved = ResolvedPackage(package, release, ())
    plan = ResolutionPlan(package_id, (resolved,))

    work_root = app_dir / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="private-prepare-", dir=work_root))
    try:
        destination = work_dir / "assets" / archive.name
        if progress:
            progress(f"Downloading {package.name} {release.version}: {archive.name}")
        if client.has_signing_identity:
            client.key_status_snapshot()
        client.download_archive(
            archive.download_path,
            github_user_id,
            destination,
            version=str(release.version),
            expected_size=archive.size,
            progress=progress,
        )
        actual_archive_digest = sha256_file(destination)
        if actual_archive_digest != archive.sha256:
            raise DownloadError(
                f"private package SHA-256 mismatch: expected {archive.sha256}, got {actual_archive_digest}"
            )
        scanned, ignored = PackageScanner().scan(package, destination, work_dir / "scan")
        actual = {item.target.casefold(): item.sha256 for item in scanned}
        if expected and actual != expected:
            raise DownloadError("private package contents do not match the authorized file manifest")
        prepared_asset = PreparedAsset(
            asset=asset,
            path=destination,
            sha256=actual_archive_digest,
            publisher_verified=False,
            publisher_digest=archive.sha256,
        )
        prepared_package = PreparedPackage(
            resolved=resolved,
            assets=[prepared_asset],
            files=scanned,
            ignored_files=ignored,
        )
        return PreparedPlan(plan, [prepared_package], work_dir)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
