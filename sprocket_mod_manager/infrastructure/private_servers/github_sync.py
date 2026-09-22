from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sprocket_mod_manager.infrastructure.private_servers import MAX_RESPONSE_BYTES

GITHUB_OAUTH_CLIENT_ID = "Ov23liKLQ4rbbEKXRkCQ"
GITHUB_GIST_FILENAME = "sprocket-mod-manager-servers.json"


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


def _github_json_request(
        method: str,
        url: str,
        access_token: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int = 10,
) -> Any:
    token = str(access_token).strip()
    if not token:
        raise ValueError("GitHub access token is empty")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, method=method, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "sprocket-mod-manager",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ValueError(
            f"GitHub Gist request failed: {getattr(exc, 'code', '') or getattr(exc, 'reason', exc)}") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("GitHub response is too large")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub returned invalid JSON") from exc


def github_gist_sync(
        access_token: str,
        entries: list[dict[str, Any]],
        gist_id: str = "",
        *,
        timeout: int = 10,
        return_conflicts: bool = False,
) -> tuple[str, list[dict[str, Any]]] | tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    def recovery_entry(item: dict[str, Any]) -> dict[str, Any]:
        allowed = ("server_id", "url", "name", "public_key_fingerprint", "updated_at", "deleted")
        result: dict[str, Any] = {
            field: str(item[field]).strip()
            for field in allowed
            if str(item.get(field, "")).strip()
        }
        if "updated_at" in item:
            try:
                result["updated_at"] = max(0, int(item["updated_at"]))
            except (TypeError, ValueError):
                result.pop("updated_at", None)
        if item.get("deleted") is True:
            result["deleted"] = True
        else:
            result.pop("deleted", None)
        return result

    local = {
        str(item.get("server_id")): recovery_entry(item)
        for item in entries
        if isinstance(item, dict) and str(item.get("server_id", "")).strip()
    }
    remote: dict[str, Any] = {}
    resolved_id = str(gist_id).strip()
    if resolved_id:
        value = _github_json_request("GET", f"https://api.github.com/gists/{resolved_id}", access_token,
                                     timeout=timeout)
        files = value.get("files", {}) if isinstance(value, dict) else {}
        content = files.get(GITHUB_GIST_FILENAME, {}).get("content", "") if isinstance(files, dict) else ""
        try:
            parsed = json.loads(content) if content else {}
            remote = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            remote = {}
    else:
        listed = _github_json_request("GET", "https://api.github.com/gists?per_page=100", access_token, timeout=timeout)
        if isinstance(listed, list):
            for gist in listed:
                files = gist.get("files", {}) if isinstance(gist, dict) else {}
                if isinstance(files, dict) and GITHUB_GIST_FILENAME in files:
                    resolved_id = str(gist.get("id", ""))
                    file_info = files[GITHUB_GIST_FILENAME]
                    inline = file_info.get("content", "") if isinstance(file_info, dict) else ""
                    if inline:
                        try:
                            parsed = json.loads(inline)
                            remote = parsed if isinstance(parsed, dict) else {}
                        except json.JSONDecodeError:
                            remote = {}
                    else:
                        content = file_info.get("raw_url", "") if isinstance(file_info, dict) else ""
                        if content:
                            raw = _github_json_request("GET", content, access_token, timeout=timeout)
                            remote = raw if isinstance(raw, dict) else {}
                    break
    remote_entries = {
        str(item.get("server_id")): recovery_entry(item)
        for item in remote.get("servers", []) if isinstance(remote.get("servers"), list)
        if isinstance(item, dict) and str(item.get("server_id", "")).strip()
    }
    conflicts: list[dict[str, Any]] = []
    for server_id, remote_item in remote_entries.items():
        local_item = local.get(server_id)
        if local_item is None:
            local[server_id] = remote_item
            continue
        identity_fields = ("url", "name", "public_key_fingerprint")
        if any(local_item.get(field, "") != remote_item.get(field, "") for field in identity_fields):
            conflicts.append({
                "server_id": server_id,
                "local": local_item,
                "remote": remote_item,
            })
            continue
        local_time = int(local_item.get("updated_at", "0") or 0)
        remote_time = int(remote_item.get("updated_at", "0") or 0)
        if remote_time > local_time:
            local[server_id] = remote_item
    document = {"schema_version": 1, "servers": sorted(local.values(), key=lambda item: str(item.get("server_id", "")))}
    body = {"description": "Sprocket Mod Manager private server recovery", "public": False,
            "files": {GITHUB_GIST_FILENAME: {"content": json.dumps(document, ensure_ascii=False, indent=2) + "\n"}}}
    if resolved_id:
        _github_json_request("PATCH", f"https://api.github.com/gists/{resolved_id}", access_token, body,
                             timeout=timeout)
    else:
        created = _github_json_request("POST", "https://api.github.com/gists", access_token, body, timeout=timeout)
        resolved_id = str(created.get("id", "")) if isinstance(created, dict) else ""
        if not resolved_id:
            raise ValueError("GitHub did not return a Gist id")
    if return_conflicts:
        return resolved_id, document["servers"], conflicts
    return resolved_id, document["servers"]
