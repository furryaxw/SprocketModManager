from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from .github import HttpClient
from ..domain.errors import RegistryError
from ..domain.registry import Registry
from ..utilities.urls import is_loopback_host


class RegistrySourceLoader:
    """Load registries while enforcing the transport boundary in one place."""

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def load(self, source: str | Path, *, refresh: bool = False) -> Registry:
        if isinstance(source, Path) or (isinstance(source, str) and not urlparse(source).scheme):
            return Registry.from_file(Path(source))

        url = str(source)
        parsed = urlparse(url)
        is_loopback_http = parsed.scheme == "http" and is_loopback_host(parsed.hostname)
        if parsed.scheme != "https" and not is_loopback_http:
            raise RegistryError("registry URL must use HTTPS or loopback HTTP")
        data = self.http.get_bytes(
            url,
            accept="application/json",
            max_bytes=16 * 1024 * 1024,
            cache_seconds=0 if refresh else 300,
            allow_loopback_http=True,
        )
        try:
            return Registry.from_dict(json.loads(data.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RegistryError(f"invalid registry JSON: {exc}") from exc
