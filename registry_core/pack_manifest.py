"""整合包的清单：release 里那份独立的 JSON 资产（`*.json`）。

成员与落点规则都写在清单里 —— 那是包自己的描述，所以作者改成员、改落点、加新目录都不需要动
注册表条目。索引构建按 release 把清单并进发布的条目（`when` 取 `=该版本`），客户端与站点因此
只看索引；清单取不到时退回条目里可选的基线。

这里只管解析与形状校验（大小、`schema_version`、成员形状）；落点规则的类型/路径/落点由合并进
条目的那一侧用注册表自己的校验器检查，不在这里造第三套。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .errors import ScanError

MANIFEST_SCHEMA_VERSION = 1
# 清单只是一份成员与规则的列表：比这更大说明拿错了文件。
MAX_MANIFEST_BYTES = 256 * 1024
MANIFEST_SUFFIX = ".json"


@dataclass(frozen=True)
class PackManifest:
    members: tuple[dict[str, str], ...] = ()
    install: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.members and not self.install


def parse_pack_manifest(raw: bytes, *, source: str = "pack manifest") -> PackManifest:
    """解析清单字节；结构不合法一律抛 `ScanError`。

    清单是包自己的声明：读不动就说明这个包坏了，报错比猜强。
    """
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ScanError(f"{source}: manifest is larger than {MAX_MANIFEST_BYTES} bytes")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScanError(f"{source}: manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScanError(f"{source}: manifest must be an object")
    version = payload.get("schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        raise ScanError(f"{source}: unsupported manifest schema_version: {version!r}")

    members = _members(payload.get("members"), source=source)
    install = payload.get("install", {})
    if install is None:
        install = {}
    if not isinstance(install, dict):
        raise ScanError(f"{source}: manifest install must be an object")
    mode = install.get("mode", "standard")
    if mode not in {"standard", "patch"}:
        raise ScanError(f"{source}: manifest install mode is invalid: {mode!r}")
    return PackManifest(members=members, install=dict(install))


def _members(raw: object, *, source: str) -> tuple[dict[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ScanError(f"{source}: manifest members must be a list")
    members: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ScanError(f"{source}: manifest members must be objects")
        member_id = item.get("id")
        version_range = item.get("version")
        if not isinstance(member_id, str) or not member_id.strip():
            raise ScanError(f"{source}: manifest member needs an id")
        if not isinstance(version_range, str) or not version_range.strip():
            raise ScanError(f"{source}: manifest member {member_id} needs a version")
        when = item.get("when", "*")
        if not isinstance(when, str) or not when.strip():
            when = "*"
        key = (member_id, when)
        if key in seen:
            raise ScanError(f"{source}: manifest lists member {member_id} twice for {when}")
        seen.add(key)
        members.append({"id": member_id, "version": version_range, "when": when})
    return tuple(members)
