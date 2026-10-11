from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .github import HttpClient
from ..domain.errors import RegistryError
from ..domain.registry import Registry
from ..utilities.urls import is_loopback_host

# 索引目录里的三份文件：包列表、游戏与加载器环境、诊断规则包。
DOCUMENT_NAMES = ("packages.json", "environment.json", "diagnosis.json")
PACKAGES_NAME, ENVIRONMENT_NAME, DIAGNOSIS_NAME = DOCUMENT_NAMES


class RegistrySourceLoader:
    """Load registries while enforcing the transport boundary in one place.

    输入就是放着那三份文件的目录：本地目录，或 HTTPS（回环 HTTP 也可以）地址前缀。
    它们分别取回，再合成 `Registry.from_dict` 认的那一份形状 —— 拆分只活在传输层。
    """

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def load(
            self,
            source: str | Path,
            *,
            refresh: bool = False,
            documents: Sequence[str] = DOCUMENT_NAMES,
    ) -> Registry:
        if isinstance(source, Path):
            return self._load_directory(source, documents=documents)
        text = str(source)
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"}:
            return self._load_base_url(text, refresh=refresh, documents=documents)
        if "://" in text:
            raise RegistryError("registry URL must use HTTPS or loopback HTTP")
        # Windows 盘符会被 urlparse 当成 scheme，所以只有带 `://` 的写法才算 URL。
        return self._load_directory(Path(text), documents=documents)

    def _load_directory(self, directory: Path, *, documents: Sequence[str] = DOCUMENT_NAMES) -> Registry:
        if not directory.is_dir():
            raise RegistryError(f"registry directory does not exist: {directory}")
        loaded: dict[str, Any] = {}
        for name in documents:
            path = directory / name
            try:
                loaded[name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RegistryError(f"cannot read registry {path}: {exc}") from exc
        return self._merge(loaded, required=documents)

    def _load_base_url(
            self,
            base: str,
            *,
            refresh: bool,
            documents: Sequence[str] = DOCUMENT_NAMES,
    ) -> Registry:
        parsed = urlparse(base)
        is_loopback_http = parsed.scheme == "http" and is_loopback_host(parsed.hostname)
        if parsed.scheme != "https" and not is_loopback_http:
            raise RegistryError("registry URL must use HTTPS or loopback HTTP")
        root = base.rstrip("/")
        # 每份文件各走一次网络：串行时每一份的往返都要等前一份回来，并行取只花最慢的那一份。
        pool = ThreadPoolExecutor(max_workers=len(documents))
        futures = {
            name: pool.submit(
                self.http.get_bytes,
                f"{root}/{name}",
                accept="application/json",
                max_bytes=16 * 1024 * 1024,
                cache_seconds=0 if refresh else 300,
                allow_loopback_http=True,
            )
            for name in documents
        }
        loaded: dict[str, Any] = {}
        try:
            for name in documents:
                data = futures[name].result()
                try:
                    loaded[name] = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RegistryError(f"invalid registry JSON: {exc}") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return self._merge(loaded, required=documents)

    def _merge(
            self,
            loaded: dict[str, Any],
            *,
            required: Sequence[str] = DOCUMENT_NAMES,
    ) -> Registry:
        """只校验这次真的取了的文件：没取的留空，读出来的那份 Registry 少那几块事实。"""
        for name in required:
            if not isinstance(loaded.get(name), dict):
                raise RegistryError(f"registry file must be an object: {name}")
        packages = loaded.get(PACKAGES_NAME) or {}
        environment = loaded.get(ENVIRONMENT_NAME) or {}
        return Registry.from_dict(
            {
                "schema_version": packages.get("schema_version"),
                "generated_at": packages.get("generated_at", ""),
                "packages": packages.get("packages"),
                "game": environment.get("game"),
                "providers": environment.get("providers") or {},
                "diagnosis": loaded.get(DIAGNOSIS_NAME) or {},
            }
        )
