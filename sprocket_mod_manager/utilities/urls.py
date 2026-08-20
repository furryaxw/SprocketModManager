from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def normalize_proxy_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
    ):
        raise ValueError("proxy URL must be an HTTP(S) server URL")
    return text.rstrip("/")


def normalize_github_proxy_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("GitHub proxy URL must use HTTPS")
    return text.rstrip("/") + "/"
