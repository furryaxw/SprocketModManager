from __future__ import annotations

from .http_client import GITHUB_ASSET_HOSTS, HttpClient
from ..domain.errors import DownloadError
from ..domain.models import RegistryPackage, ReleaseAsset, ReleaseInfo
from ..utilities.checksums import SHA256_PATTERN, parse_checksum_text


def release_asset_sha256(asset: ReleaseAsset) -> str | None:
    if not asset.digest:
        return None
    algorithm, separator, value = asset.digest.partition(":")
    if separator and algorithm.casefold() == "sha256" and SHA256_PATTERN.fullmatch(value):
        return value.casefold()
    return None


def publisher_checksum(
        http: HttpClient,
        package: RegistryPackage,
        release: ReleaseInfo,
        asset: ReleaseAsset,
) -> tuple[str, str] | None:
    asset_digest = release_asset_sha256(asset)
    if asset_digest:
        return asset_digest, "Developer server manifest digest" if not package.repository else "GitHub Release digest"

    sidecar_names = {
        f"{asset.name}.sha256".casefold(),
        "sha256sums",
        "sha256sums.txt",
        "checksums.txt",
    }
    for sidecar in release.assets:
        if sidecar.name.casefold() not in sidecar_names:
            continue
        try:
            content = http.get_bytes(
                sidecar.download_url,
                timeout=30,
                max_bytes=1024 * 1024,
                allowed_hosts=GITHUB_ASSET_HOSTS,
            ).decode("utf-8-sig")
        except (DownloadError, UnicodeDecodeError):
            continue
        checksum = parse_checksum_text(
            content,
            asset.name,
            allow_bare=sidecar.name.casefold() == f"{asset.name}.sha256".casefold(),
        )
        if checksum:
            return checksum, sidecar.name
    return None
