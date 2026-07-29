from __future__ import annotations

import fnmatch
import hashlib
import ipaddress
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from .errors import DownloadError, RegistryError
from .models import ProgressCallback, RegistryPackage, ReleaseAsset, ReleaseInfo
from .semver import Version


USER_AGENT = "sprocket-mod-manager/0.1"
MAX_API_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ASSET_BYTES = 1024 * 1024 * 1024
GITHUB_ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com"}
GITHUB_RELEASE_CACHE_SECONDS = 60 * 60


@dataclass(frozen=True)
class RepositoryRelease:
    tag: str
    version: Version
    page_url: str


def is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


class HttpClient:
    def __init__(self, cache_dir: Path, token: str | None = None):
        self.cache_dir = cache_dir
        self.token = token or os.environ.get("GITHUB_TOKEN")

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / "http" / f"{key}.json", self.cache_dir / "http" / f"{key}.body"

    @staticmethod
    def _validate_https(
        url: str,
        allowed_hosts: set[str] | None = None,
        *,
        allow_loopback_http: bool = False,
    ) -> None:
        parsed = urlparse(url)
        is_https = parsed.scheme == "https" and bool(parsed.hostname)
        is_local_http = (
            allow_loopback_http
            and parsed.scheme == "http"
            and is_loopback_host(parsed.hostname)
        )
        if not is_https and not is_local_http:
            raise DownloadError(f"refusing non-HTTPS URL: {url}")
        if allowed_hosts and parsed.hostname.casefold() not in {host.casefold() for host in allowed_hosts}:
            raise DownloadError(f"download host is not allowed: {parsed.hostname}")

    def get_bytes(
        self,
        url: str,
        *,
        accept: str = "application/octet-stream",
        timeout: int = 30,
        max_bytes: int = MAX_API_RESPONSE_BYTES,
        cache_seconds: int = 0,
        allowed_hosts: set[str] | None = None,
        allow_loopback_http: bool = False,
        progress: ProgressCallback | None = None,
    ) -> bytes:
        self._validate_https(
            url,
            allowed_hosts,
            allow_loopback_http=allow_loopback_http,
        )
        meta_path, body_path = self._cache_paths(url)
        cached_meta: dict[str, Any] = {}
        if meta_path.is_file() and body_path.is_file():
            try:
                cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if cache_seconds and time.time() - float(cached_meta.get("fetched_at", 0)) < cache_seconds:
                    return body_path.read_bytes()
            except (OSError, ValueError, json.JSONDecodeError):
                cached_meta = {}

        headers = {"Accept": accept, "User-Agent": USER_AGENT}
        request_host = (urlparse(url).hostname or "").casefold()
        if self.token and request_host == "api.github.com":
            headers["Authorization"] = f"Bearer {self.token}"
        if cached_meta.get("etag"):
            headers["If-None-Match"] = str(cached_meta["etag"])
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                self._validate_https(
                    response.geturl(),
                    allowed_hosts,
                    allow_loopback_http=allow_loopback_http,
                )
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise DownloadError(f"response is too large: {url}")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadError(f"response exceeded size limit: {url}")
                    chunks.append(chunk)
                    if progress:
                        progress(f"downloaded {total:,} bytes")
                body = b"".join(chunks)
                _atomic_write(body_path, body)
                meta = {
                    "url": url,
                    "etag": response.headers.get("ETag"),
                    "fetched_at": time.time(),
                }
                _atomic_write(meta_path, json.dumps(meta, indent=2).encode("utf-8"))
                return body
        except HTTPError as exc:
            if exc.code == 304 and body_path.is_file():
                cached_meta["fetched_at"] = time.time()
                _atomic_write(meta_path, json.dumps(cached_meta, indent=2).encode("utf-8"))
                return body_path.read_bytes()
            if body_path.is_file() and exc.code in {403, 429, 500, 502, 503, 504}:
                return body_path.read_bytes()
            raise DownloadError(f"HTTP {exc.code} for {url}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if body_path.is_file():
                return body_path.read_bytes()
            raise DownloadError(f"request failed for {url}: {exc}") from exc

    def get_json(self, url: str, *, cache_seconds: int = 600) -> Any:
        data = self.get_bytes(
            url,
            accept="application/vnd.github+json",
            cache_seconds=cache_seconds,
            allowed_hosts={"api.github.com"},
        )
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DownloadError(f"invalid JSON response from {url}") from exc

    def download(self, asset: ReleaseAsset, destination: Path, progress: ProgressCallback | None = None) -> Path:
        if asset.size < 0 or asset.size > MAX_ASSET_BYTES:
            raise DownloadError(f"asset size is outside the allowed range: {asset.name}")
        data = self.get_bytes(
            asset.download_url,
            timeout=180,
            max_bytes=max(asset.size + 1024 * 1024, 1024 * 1024),
            allowed_hosts=GITHUB_ASSET_HOSTS,
            progress=progress,
        )
        if asset.size and len(data) != asset.size:
            raise DownloadError(
                f"asset size mismatch for {asset.name}: expected {asset.size}, got {len(data)}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(destination, data)
        return destination


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
                if parsed.scheme != "https" or parsed.hostname != "github.com" or not parsed.path.casefold().startswith(expected_prefix):
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

    @staticmethod
    def install_assets(package: RegistryPackage, release: ReleaseInfo) -> tuple[ReleaseAsset, ...]:
        rules = package.release.get("assets", {})
        includes = tuple(str(pattern).casefold() for pattern in rules.get("include", ()))
        excludes = tuple(str(pattern).casefold() for pattern in rules.get("exclude", ()))
        selected = []
        for asset in release.assets:
            name = asset.name.casefold()
            if Path(asset.name).suffix.casefold() not in {".dll", ".zip", ".smod"}:
                continue
            if not any(fnmatch.fnmatchcase(name, pattern) for pattern in includes):
                continue
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes):
                continue
            selected.append(asset)
        return tuple(selected)
