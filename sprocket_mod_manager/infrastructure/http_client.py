from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from ..domain.errors import DownloadError
from ..domain.models import ProgressCallback, ReleaseAsset
from ..utilities.urls import is_loopback_host, normalize_github_proxy_url, normalize_proxy_url

USER_AGENT = "sprocket-mod-manager/0.1"
MAX_API_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ASSET_BYTES = 1024 * 1024 * 1024
GITHUB_ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com"}
GITHUB_RELEASE_CACHE_SECONDS = 60 * 60
LOGGER = logging.getLogger(__name__)


def _log_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


class HttpClient:
    def __init__(
            self,
            cache_dir: Path,
            token: str | None = None,
            *,
            proxy_url: str = "",
            github_proxy_url: str = "",
    ):
        self.cache_dir = cache_dir
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.proxy_url = ""
        self.github_proxy_url = ""
        self._opener = None
        self.configure_network(proxy_url, github_proxy_url)

    def configure_network(self, proxy_url: str = "", github_proxy_url: str = "") -> None:
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.github_proxy_url = normalize_github_proxy_url(github_proxy_url)
        self._opener = (
            build_opener(ProxyHandler({"http": self.proxy_url, "https": self.proxy_url}))
            if self.proxy_url
            else None
        )
        LOGGER.info(
            "network configuration updated proxy=%s github_proxy=%s",
            "enabled" if self.proxy_url else "disabled",
            "enabled" if self.github_proxy_url else "disabled",
        )

    def _open(self, request: Request, timeout: int):
        if self._opener is not None:
            return self._opener.open(request, timeout=timeout)
        return urlopen(request, timeout=timeout)

    def _cache_paths(self, url: str, accept: str = "") -> tuple[Path, Path]:
        key = hashlib.sha256(f"{url}\0{accept}".encode("utf-8")).hexdigest()
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
        safe_url = _log_url(url)
        LOGGER.debug("HTTP fetch started url=%s cache_seconds=%d", safe_url, cache_seconds)
        self._validate_https(
            url,
            allowed_hosts,
            allow_loopback_http=allow_loopback_http,
        )
        meta_path, body_path = self._cache_paths(url, accept)
        cached_meta: dict[str, Any] = {}
        if meta_path.is_file() and body_path.is_file():
            try:
                cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if cache_seconds and time.time() - float(cached_meta.get("fetched_at", 0)) < cache_seconds:
                    LOGGER.debug("HTTP cache hit url=%s", safe_url)
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
            with self._open(request, timeout=timeout) as response:
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
                LOGGER.debug("HTTP fetch completed url=%s bytes=%d", safe_url, len(body))
                return body
        except HTTPError as exc:
            if exc.code == 304 and body_path.is_file():
                LOGGER.debug("HTTP cache revalidated url=%s", safe_url)
                cached_meta["fetched_at"] = time.time()
                _atomic_write(meta_path, json.dumps(cached_meta, indent=2).encode("utf-8"))
                return body_path.read_bytes()
            if body_path.is_file() and exc.code in {403, 429, 500, 502, 503, 504}:
                LOGGER.warning("HTTP %d; using stale cache url=%s", exc.code, safe_url)
                return body_path.read_bytes()
            LOGGER.error("HTTP request failed status=%d url=%s", exc.code, safe_url)
            raise DownloadError(f"HTTP {exc.code} for {url}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if body_path.is_file():
                LOGGER.warning("HTTP request failed; using stale cache url=%s error=%s", safe_url, exc)
                return body_path.read_bytes()
            LOGGER.error("HTTP request failed url=%s error=%s", safe_url, exc)
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

    def download(
            self,
            asset: ReleaseAsset,
            destination: Path,
            progress: ProgressCallback | None = None,
            *,
            hosts: set[str] | None = None,
    ) -> Path:
        LOGGER.info("asset download started name=%s size=%d", asset.name, asset.size)
        # 允许的主机 = 这个包声明的来源 + GitHub 自己的资产 CDN：`github.com/<owner>/<repo>/releases/download/...`
        # 会 302 到 `release-assets.githubusercontent.com`，重定向后的地址同样要能过校验。
        allowed_hosts = {host.casefold() for host in (hosts or ())} | GITHUB_ASSET_HOSTS
        if asset.size < 0 or asset.size > MAX_ASSET_BYTES:
            raise DownloadError(f"asset size is outside the allowed range: {asset.name}")
        # 没有声明大小的资产（例如外部来源只给了摘要）按全局上限读，不能用 0 反推成一个更小的上限。
        declared_size = asset.size or MAX_ASSET_BYTES
        self._validate_https(asset.download_url, allowed_hosts)
        request_url = asset.download_url
        asset_host = (urlparse(asset.download_url).hostname or "").casefold()
        # 加速器只镜像 GitHub 自己的资产地址；外部来源的地址必须原样请求，不能挂到它下面。
        if self.github_proxy_url and asset_host in GITHUB_ASSET_HOSTS:
            request_url = f"{self.github_proxy_url}{asset.download_url}"
            proxy_host = urlparse(self.github_proxy_url).hostname
            if proxy_host:
                allowed_hosts.add(proxy_host)
        data = self.get_bytes(
            request_url,
            timeout=180,
            max_bytes=declared_size + 1024 * 1024,
            allowed_hosts=allowed_hosts,
            progress=progress,
        )
        if asset.size and len(data) != asset.size:
            raise DownloadError(
                f"asset size mismatch for {asset.name}: expected {asset.size}, got {len(data)}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(destination, data)
        LOGGER.info("asset download completed name=%s bytes=%d", asset.name, len(data))
        return destination
