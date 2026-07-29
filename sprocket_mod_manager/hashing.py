from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .errors import DownloadError
from .github import GITHUB_ASSET_HOSTS, HttpClient
from .models import RegistryPackage, ReleaseAsset, ReleaseInfo


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_asset_sha256(asset: ReleaseAsset) -> str | None:
    if not asset.digest:
        return None
    algorithm, separator, value = asset.digest.partition(":")
    if separator and algorithm.casefold() == "sha256" and _SHA256_RE.fullmatch(value):
        return value.casefold()
    return None


def _parse_checksum(text: str, asset_name: str, allow_bare: bool) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if allow_bare and _SHA256_RE.fullmatch(line):
            return line.casefold()
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if match and Path(match.group(2).strip()).name.casefold() == asset_name.casefold():
            return match.group(1).casefold()
        match = re.fullmatch(r"SHA256\s*\((.+)\)\s*=\s*([0-9a-fA-F]{64})", line, re.I)
        if match and Path(match.group(1).strip()).name.casefold() == asset_name.casefold():
            return match.group(2).casefold()
    return None


def publisher_checksum(
    http: HttpClient,
    package: RegistryPackage,
    release: ReleaseInfo,
    asset: ReleaseAsset,
) -> tuple[str, str] | None:
    asset_digest = release_asset_sha256(asset)
    if asset_digest:
        return asset_digest, "GitHub Release digest"

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
        checksum = _parse_checksum(
            content,
            asset.name,
            allow_bare=sidecar.name.casefold() == f"{asset.name}.sha256".casefold(),
        )
        if checksum:
            return checksum, sidecar.name
    return None
