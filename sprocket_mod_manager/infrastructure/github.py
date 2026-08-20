from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .http_client import GITHUB_ASSET_HOSTS, GITHUB_RELEASE_CACHE_SECONDS, HttpClient
from ..domain.errors import DownloadError, RegistryError
from ..domain.models import RegistryPackage, ReleaseAsset, ReleaseInfo
from ..domain.semver import Version


@dataclass(frozen=True)
class RepositoryRelease:
    tag: str
    version: Version
    page_url: str


@dataclass(frozen=True)
class RepositoryReadme:
    html: str
    page_url: str


class GitHubClient:
    def __init__(self, http: HttpClient):
        self.http = http
        self._release_cache: dict[str, tuple[ReleaseInfo, ...]] = {}

    def releases(self, package: RegistryPackage, refresh: bool = False) -> tuple[ReleaseInfo, ...]:
        if package.releases is not None:
            self._release_cache[package.id] = package.releases
            return package.releases
        if package.id in self._release_cache and not refresh:
            return self._release_cache[package.id]
        try:
            version_pattern = re.compile(package.release["version_pattern"])
        except (KeyError, re.error) as exc:
            raise RegistryError(f"{package.id}: invalid release version pattern") from exc

        owner, repository = package.repository.split("/", 1)
        records: list[dict[str, Any]] = []
        for page in range(1, 6):
            url = (
                "https://api.github.com/repos/"
                f"{quote(owner, safe='')}/{quote(repository, safe='')}/releases?per_page=100&page={page}"
            )
            result = self.http.get_json(
                url,
                cache_seconds=0 if refresh else GITHUB_RELEASE_CACHE_SECONDS,
            )
            if not isinstance(result, list):
                raise DownloadError(f"GitHub releases response is not a list: {package.repository}")
            records.extend(item for item in result if isinstance(item, dict))
            if len(result) < 100:
                break

        if not records:
            latest_url = (
                "https://api.github.com/repos/"
                f"{quote(owner, safe='')}/{quote(repository, safe='')}/releases/latest"
            )
            try:
                latest = self.http.get_json(
                    latest_url,
                    cache_seconds=0 if refresh else GITHUB_RELEASE_CACHE_SECONDS,
                )
            except DownloadError:
                latest = None
            if isinstance(latest, dict):
                records.append(latest)

        include_prerelease = bool(package.release.get("include_prerelease"))
        releases: list[ReleaseInfo] = []
        for record in records:
            if record.get("draft") or (record.get("prerelease") and not include_prerelease):
                continue
            tag = str(record.get("tag_name", ""))
            match = version_pattern.fullmatch(tag)
            if not match:
                continue
            try:
                version = Version.parse(match.group(1))
            except (IndexError, ValueError):
                continue
            if version.prerelease and not include_prerelease:
                continue
            assets: list[ReleaseAsset] = []
            for raw_asset in record.get("assets") or []:
                if not isinstance(raw_asset, dict):
                    continue
                url = str(raw_asset.get("browser_download_url", ""))
                parsed = urlparse(url)
                expected_prefix = f"/{package.repository}/releases/download/".casefold()
                if parsed.scheme != "https" or parsed.hostname != "github.com" or not parsed.path.casefold().startswith(
                        expected_prefix):
                    continue
                assets.append(
                    ReleaseAsset(
                        id=int(raw_asset.get("id", 0)),
                        name=str(raw_asset.get("name", "")),
                        size=int(raw_asset.get("size", 0)),
                        download_url=url,
                        digest=raw_asset.get("digest") or None,
                        updated_at=str(raw_asset.get("updated_at", "")),
                    )
                )
            releases.append(
                ReleaseInfo(
                    id=int(record.get("id", 0)),
                    tag=tag,
                    version=version,
                    prerelease=bool(record.get("prerelease")),
                    published_at=str(record.get("published_at", "")),
                    assets=tuple(assets),
                    page_url=str(record.get("html_url", "")),
                )
            )
        releases.sort(key=lambda item: item.version, reverse=True)
        self._release_cache[package.id] = tuple(releases)
        return self._release_cache[package.id]

    def latest_repository_release(self, repository: str) -> RepositoryRelease:
        parts = repository.split("/")
        if len(parts) != 2 or not all(parts):
            raise DownloadError(f"invalid GitHub repository: {repository}")
        owner, name = parts
        url = (
            "https://api.github.com/repos/"
            f"{quote(owner, safe='')}/{quote(name, safe='')}/releases/latest"
        )
        record = self.http.get_json(url, cache_seconds=3600)
        if not isinstance(record, dict) or record.get("draft") or record.get("prerelease"):
            raise DownloadError(f"invalid latest Release response: {repository}")

        tag = str(record.get("tag_name", "")).strip()
        version_text = tag[1:] if tag[:1].casefold() == "v" else tag
        try:
            version = Version.parse(version_text)
        except ValueError as exc:
            raise DownloadError(f"latest Release tag is not SemVer: {tag or '-'}") from exc

        page_url = str(record.get("html_url", ""))
        parsed = urlparse(page_url)
        expected_path = f"/{repository}/releases/tag/".casefold()
        if (
                parsed.scheme != "https"
                or (parsed.hostname or "").casefold() != "github.com"
                or not parsed.path.casefold().startswith(expected_path)
        ):
            raise DownloadError(f"invalid GitHub release page URL: {page_url or '-'}")
        return RepositoryRelease(tag=tag, version=version, page_url=page_url)

    def repository_readme(
            self,
            repository: str,
            *,
            refresh: bool = False,
    ) -> RepositoryReadme:
        parts = repository.split("/")
        if len(parts) != 2 or not all(parts):
            raise DownloadError(f"invalid GitHub repository: {repository}")
        owner, name = parts
        url = (
            "https://api.github.com/repos/"
            f"{quote(owner, safe='')}/{quote(name, safe='')}/readme"
        )
        data = self.http.get_bytes(
            url,
            accept="application/vnd.github.html+json",
            max_bytes=4 * 1024 * 1024,
            cache_seconds=0 if refresh else GITHUB_RELEASE_CACHE_SECONDS,
            allowed_hosts={"api.github.com"},
        )
        try:
            html = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DownloadError(f"GitHub README is not UTF-8: {repository}") from exc
        if not html.strip():
            raise DownloadError(f"GitHub README is empty: {repository}")
        return RepositoryReadme(
            html=html,
            page_url=f"https://github.com/{repository}#readme",
        )

    @staticmethod
    def install_assets(package: RegistryPackage, release: ReleaseInfo) -> tuple[ReleaseAsset, ...]:
        rules = package.release.get("assets", {})
        includes = tuple(str(pattern).casefold() for pattern in rules.get("include", ()))
        excludes = tuple(str(pattern).casefold() for pattern in rules.get("exclude", ()))
        selected = []
        for asset in release.assets:
            name = asset.name.casefold()
            if Path(asset.name).suffix.casefold() not in {".dll", ".zip"}:
                continue
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in includes):
                continue
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes):
                continue
            selected.append(asset)
        return tuple(selected)
