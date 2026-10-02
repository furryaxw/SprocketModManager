from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .constants import MAX_RESPONSE_BYTES

GITHUB_OAUTH_CLIENT_ID = "Ov23liKLQ4rbbEKXRkCQ"


class GitHubLoginExpired(ValueError):
    """The stored refresh token is no longer usable; a new device authorization is required."""


def _github_form_request(url: str, values: dict[str, str], *, timeout: int = 10) -> dict[str, Any]:
    data = urlencode(values).encode("ascii")
    request = Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "sprocket-mod-manager",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read(MAX_RESPONSE_BYTES).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and payload.get("error"):
            return payload
        raise ValueError(f"GitHub request failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"cannot connect to GitHub: {getattr(exc, 'reason', exc)}") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("GitHub response is too large")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("GitHub response must be an object")
    return value


def github_device_start(client_id: str, *, timeout: int = 10) -> dict[str, Any]:
    client_id = str(client_id).strip()
    if not client_id:
        raise ValueError("developer server did not provide a GitHub client id")
    value = _github_form_request(
        "https://github.com/login/device/code",
        {"client_id": client_id, "scope": "read:user gist"},
        timeout=timeout,
    )
    if value.get("error"):
        raise ValueError(str(value.get("error_description") or value["error"]))
    required = ("device_code", "user_code", "verification_uri")
    if any(not str(value.get(field, "")).strip() for field in required):
        raise ValueError("GitHub device flow response is incomplete")
    return value


def github_device_poll(client_id: str, device_code: str, *, timeout: int = 10) -> dict[str, Any]:
    value = _github_form_request(
        "https://github.com/login/oauth/access_token",
        {"client_id": str(client_id).strip(), "device_code": str(device_code).strip(),
         "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
        timeout=timeout,
    )
    if value.get("error"):
        return value
    token = str(value.get("access_token", "")).strip()
    if not token:
        raise ValueError("GitHub device flow response did not contain an access token")
    return value


def github_token_refresh(client_id: str, refresh_token: str, *, timeout: int = 10) -> dict[str, Any]:
    """Replace an expiring device-flow token.

    Refresh replies carry the next refresh token and invalidate the one that was sent, so the
    caller must persist the whole reply. A device-flow token needs no client secret.
    """
    client_id = str(client_id).strip()
    refresh_token = str(refresh_token).strip()
    if not client_id or not refresh_token:
        raise ValueError("GitHub token refresh requires a client id and a refresh token")
    value = _github_form_request(
        "https://github.com/login/oauth/access_token",
        {"client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=timeout,
    )
    if value.get("error"):
        raise GitHubLoginExpired(str(value.get("error_description") or value["error"]))
    if not str(value.get("access_token", "")).strip():
        raise ValueError("GitHub token refresh did not return an access token")
    return value


def github_current_user(access_token: str, *, timeout: int = 10) -> dict[str, Any]:
    token = str(access_token).strip()
    if not token:
        raise ValueError("GitHub access token is empty")
    request = Request(
        "https://api.github.com/user",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "sprocket-mod-manager",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read(MAX_RESPONSE_BYTES).decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            raise ValueError("GitHub access token is invalid") from exc
        raise ValueError(f"GitHub identity verification failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub identity verification failed") from exc
    if not isinstance(value, dict) or not isinstance(value.get("id"), int) or value["id"] < 1:
        raise ValueError("GitHub identity response is invalid")
    return value
