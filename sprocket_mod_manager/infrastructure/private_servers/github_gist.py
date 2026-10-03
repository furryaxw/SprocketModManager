"""私有服务器的恢复索引：把「注册过哪几台服务器」同步到本人的一个私有 Gist。

它是**恢复索引**，不是认证或权限来源：只带 `server_id`、规范化后的 `url`、展示名和公钥指纹，
绝不带访问令牌、会话、Key、包清单、下载地址或本机安装状态。换一台机器时靠它把服务器列表找回来。

合并按 `server_id`：远端多出来的条目会加进本地；身份字段（`url`/`name`/`public_key_fingerprint`）
不一致时**只记冲突、不自动改**——那可能是别人改了服务器，也可能是同名顶替，得人来认；
远端**缺**某条不代表本地要删（缺失是常态，不是删除信号）。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .constants import MAX_RESPONSE_BYTES

GITHUB_GIST_FILENAME = "sprocket-mod-manager-servers.json"
# 身份字段：这些不一致就是冲突，交给用户确认；其余字段（如 updated_at）只是合并用的元数据。
IDENTITY_FIELDS = ("url", "name", "public_key_fingerprint")


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
            f"GitHub Gist request failed: {getattr(exc, 'code', '') or getattr(exc, 'reason', exc)}"
        ) from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("GitHub response is too large")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub returned invalid JSON") from exc


def recovery_entry(item: dict[str, Any]) -> dict[str, Any]:
    """一条同步条目：只留白名单字段，其余一律丢掉（这个文件是要进别人服务器的）。"""
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


def _parse_document(content: object) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _remote_entries(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    servers = document.get("servers")
    if not isinstance(servers, list):
        return {}
    found: dict[str, dict[str, Any]] = {}
    for item in servers:
        if not isinstance(item, dict):
            continue
        server_id = str(item.get("server_id", "")).strip()
        if server_id:
            found[server_id] = recovery_entry(item)
    return found


def _read_remote(access_token: str, gist_id: str, *, timeout: int) -> tuple[str, dict[str, Any]]:
    """定位恢复 Gist 并读回文档；`gist_id` 为空时按固定文件名在本人 Gist 里找。"""
    resolved = str(gist_id).strip()
    if resolved:
        value = _github_json_request(
            "GET", f"https://api.github.com/gists/{resolved}", access_token, timeout=timeout
        )
        files = value.get("files", {}) if isinstance(value, dict) else {}
        info = files.get(GITHUB_GIST_FILENAME, {}) if isinstance(files, dict) else {}
        content = info.get("content", "") if isinstance(info, dict) else ""
        return resolved, _parse_document(content)

    listed = _github_json_request(
        "GET", "https://api.github.com/gists?per_page=100", access_token, timeout=timeout
    )
    if not isinstance(listed, list):
        return "", {}
    for gist in listed:
        if not isinstance(gist, dict):
            continue
        files = gist.get("files", {})
        if not isinstance(files, dict) or GITHUB_GIST_FILENAME not in files:
            continue
        found_id = str(gist.get("id", ""))
        info = files[GITHUB_GIST_FILENAME]
        inline = info.get("content", "") if isinstance(info, dict) else ""
        if inline:
            return found_id, _parse_document(inline)
        raw = info.get("raw_url", "") if isinstance(info, dict) else ""
        if raw:
            fetched = _github_json_request("GET", raw, access_token, timeout=timeout)
            return found_id, fetched if isinstance(fetched, dict) else {}
        return found_id, {}
    return "", {}


def github_gist_sync(
        access_token: str,
        entries: list[dict[str, Any]],
        gist_id: str = "",
        *,
        timeout: int = 10,
        return_conflicts: bool = False,
) -> tuple[str, list[dict[str, Any]]] | tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """把本地服务器列表与恢复 Gist 合并后写回；返回 Gist id 与合并后的条目。

    `return_conflicts` 打开时额外返回身份字段对不上的那些条目，由调用方去问用户。
    """
    local = {
        str(item.get("server_id")): recovery_entry(item)
        for item in entries
        if isinstance(item, dict) and str(item.get("server_id", "")).strip()
    }
    resolved_id, document = _read_remote(access_token, gist_id, timeout=timeout)

    conflicts: list[dict[str, Any]] = []
    for server_id, remote_item in _remote_entries(document).items():
        local_item = local.get(server_id)
        if local_item is None:
            local[server_id] = remote_item
            continue
        if any(local_item.get(field, "") != remote_item.get(field, "") for field in IDENTITY_FIELDS):
            conflicts.append({"server_id": server_id, "local": local_item, "remote": remote_item})
            continue
        local_time = int(local_item.get("updated_at", "0") or 0)
        remote_time = int(remote_item.get("updated_at", "0") or 0)
        if remote_time > local_time:
            local[server_id] = remote_item

    servers = sorted(local.values(), key=lambda item: str(item.get("server_id", "")))
    body = {
        "description": "SprocketModManager private server recovery",
        "public": False,
        "files": {
            GITHUB_GIST_FILENAME: {
                "content": json.dumps(
                    {"schema_version": 1, "servers": servers}, ensure_ascii=False, indent=2
                ) + "\n"
            }
        },
    }
    if resolved_id:
        _github_json_request(
            "PATCH", f"https://api.github.com/gists/{resolved_id}", access_token, body, timeout=timeout
        )
    else:
        created = _github_json_request(
            "POST", "https://api.github.com/gists", access_token, body, timeout=timeout
        )
        resolved_id = str(created.get("id", "")) if isinstance(created, dict) else ""
        if not resolved_id:
            raise ValueError("GitHub did not return a Gist id")
    if return_conflicts:
        return resolved_id, servers, conflicts
    return resolved_id, servers
