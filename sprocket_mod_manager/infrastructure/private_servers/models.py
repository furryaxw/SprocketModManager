from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ...utilities.urls import is_loopback_host


def normalize_server_url(value: object) -> str:
    """开发者服务器的 origin。

    只收 origin：路径、查询、片段和内置凭据一律拒绝，免得一个 URL 同时表达"服务器"
    和"某个端点"。HTTPS 之外只放行 loopback，供本机测试服务器使用。
    """
    text = str(value or "").strip().rstrip("/")
    parsed = urlparse(text)
    is_https = parsed.scheme == "https" and bool(parsed.hostname)
    is_loopback_http = parsed.scheme == "http" and is_loopback_host(parsed.hostname)
    if not (is_https or is_loopback_http):
        raise ValueError("developer server must use HTTPS, except for loopback test servers")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("developer server URL must not contain credentials, path, query or fragment")
    return text


@dataclass(frozen=True)
class DeveloperServerInfo:
    """`GET /v1/server-info` 的身份读数。

    `signing_identity` 是服务器自报的 Ed25519 公钥；它与本机记下的指纹是否一致、以及
    `signing_rotation` 是否能证明换钥，由客户端在读取时判定，不在这里解释。
    """

    server_id: str
    name: str
    operator: str
    protocol_version: int
    signing_identity: dict[str, Any] | None = None
    trust_method: str = ""
    key_encoding: str = ""
    manual_transport: str = ""
    signing_rotation: dict[str, Any] | None = None
    # 允许下载重定向到哪些源：裸主机名只认 https，完整 origin 按 scheme/host/port 精确匹配。
    download_origins: tuple[str, ...] = ()


__all__ = ["DeveloperServerInfo", "normalize_server_url"]
