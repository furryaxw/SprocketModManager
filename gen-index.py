#!/usr/bin/env python3
"""Validate registry metadata and build the static Pages index."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from sprocket_mod_manager.domain.diagnosis import (
    BUCKETS,
    CHECKS,
    GO_TO_PAGES,
    LOG_ROLES,
    MAX_LEVEL,
    MIN_LEVEL,
    diagnosis_pack,
)
from sprocket_mod_manager.domain.semver import Version, satisfies, validate_range

REQUIRED_FIELDS = {
    "schema_version",
    "id",
    "name",
    "install",
}
HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
FORBIDDEN_VERSION_FIELDS = {"version", "latest_version", "download_url", "tag"}
ALLOWED_TARGET_ROOTS = {"Mods", "Plugins", "UserLibs", "UserData"}
ALLOWED_CATEGORIES = {"gameplay", "utility", "library", "visual", "audio", "translation", "other"}
PATCH_MODE = "patch"
PACKAGE_KINDS = ("modfile", "modloader", "loaderbridge", "translateloader", "patch")
LOADER_KINDS = ("modloader", "loaderbridge", "translateloader", "patch")
# `provides` 里的这个字面量表示「这条发布自己的版本」。
VERSION_TEMPLATE = "{version}"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
LANGUAGE_TAG_RE = re.compile(
    r"^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|[xX](?:-[A-Za-z0-9]{1,8})+)$"
)
GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
# 上一版索引的包列表：拉取失败时从它取已知的 releases。
FALLBACK_INDEX_URL = "https://sprocketmods.furryaxw.top/data/packages.json"
INSTALLABLE_SUFFIXES = {".dll", ".zip"}

# 能力用版本化名字表达：模组的依赖区间就是对某个能力的区间。游戏那项是本地能力，
# 由索引顶层 `game.id` 给出；其余能力由加载器类包的 `provides` 声明（没写就是它自己的包 id）。
GAME_CAPABILITY_ID = "hamish.sprocket"
GAME_NAME = "Sprocket"
MELONLOADER_CAPABILITY_ID = "lavagang.melonloader"
# 能力名的别名表：基线索引的依赖与发布说明的 `sp-compat` 块里会出现
# `environment.sprocket` / `sprocket` 与 `environment.melonloader` / `melonloader`，
# 它们对的是游戏能力与 MelonLoader 能力这两项。索引使用规范 id，生成时按这张表翻译；
# 表里没有的名字原样通过。
LEGACY_CAPABILITY_NAMES = {
    "environment.sprocket": GAME_CAPABILITY_ID,
    "sprocket": GAME_CAPABILITY_ID,
    "environment.melonloader": MELONLOADER_CAPABILITY_ID,
    "melonloader": MELONLOADER_CAPABILITY_ID,
}
GAME_VERSION_SEGMENTS = 4
LOADER_VERSION_SEGMENTS = 3
FILE_TYPE_RE = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9]+)*:[a-z0-9]+(?:[.-][a-z0-9]+)*$"
)
FILE_TYPE_WILDCARD_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*:\*$")
SUPPLY_TARGET_RE = re.compile(r"^\{Sprocket\}(?:/[A-Za-z0-9._-]+)*$")
SUBPATH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
# 带预发布段的三段版本（`6.0.0-be.785`）：供给表里允许，数值区间写法放不下。
PRERELEASE_VERSION_RE = re.compile(r"\d+\.\d+\.\d+-[0-9A-Za-z]")
COMPAT_BLOCK_RE = re.compile(r"<!--\s*sp-compat\s*(?P<body>.*?)-->", re.DOTALL | re.IGNORECASE)
# 供给表：加载器包版本区间 -> 该包能跑的游戏版本区间。构建时规范化后一并写进索引，客户端解析索引即可拿到，不必额外请求。
PROVIDERS_FILE_NAME = "providers.json"
PROVIDERS_FILE = Path(__file__).resolve().parent / PROVIDERS_FILE_NAME
# 诊断规则包：日志签名与环境检查。改动靠发索引生效，所以坏条目在这里挡住，别留着让客户端猜。
DIAGNOSIS_FILE_NAME = "diagnosis.json"
DIAGNOSIS_FILE = Path(__file__).resolve().parent / DIAGNOSIS_FILE_NAME
# 兼容声明覆盖表：上游模组的 release 正文里没有 `sp-compat` 块时，用它补上区间。
OVERRIDE_FILE_NAME = "override.json"
OVERRIDE_FILE = Path(__file__).resolve().parent / OVERRIDE_FILE_NAME
# 索引目录里的三份文件：包列表、游戏与加载器环境、诊断规则包。
# 诊断那份与规则源文件同名，但落在输出目录里。
PACKAGES_FILE_NAME = "packages.json"
ENVIRONMENT_FILE_NAME = "environment.json"
# 站点把这三份放在固定的 `data/` 下：客户端拿站点根（或站点根目录）拼出它们的地址。
INDEX_DATA_DIR = "data"
# 兼容层：只读单文件的老客户端按站点根的 `index.json` 取注册表，条目声明 v2。
LEGACY_INDEX_FILE_NAME = "index.json"
LEGACY_ENTRY_SCHEMA_VERSION = 2
DIAGNOSIS_ENTRY_KEYS = frozenset(
    {"id", "bucket", "level", "title", "explain", "evidence", "tutorial", "go_to", "check", "match"}
)
DIAGNOSIS_TEXT_KEYS = ("title", "explain", "evidence", "tutorial")


def is_loader_kind(kind: object) -> bool:
    return str(kind) in LOADER_KINDS


def declared_capabilities(meta: dict[str, Any]) -> dict[str, str]:
    """这个包对外提供的能力：显式 `provides`，没写时只有加载器类包提供自己。

    模组的包 id 也是别人可以依赖的**包**，但它不是能力轴 —— 模组之间靠包依赖相连，
    能力轴只描述「这台机器能跑什么」。
    """
    provides = meta.get("provides")
    if isinstance(provides, dict) and provides:
        return {str(key): str(value) for key, value in provides.items()}
    if is_loader_kind(meta.get("kind")):
        return {str(meta["id"]): VERSION_TEMPLATE}
    return {}


def current_capability_name(name: object) -> str:
    """能力名的规范写法：命中 `LEGACY_CAPABILITY_NAMES` 就翻译，其余原样返回。"""
    text = str(name)
    return LEGACY_CAPABILITY_NAMES.get(text, text)


def migrate_release_dependencies(releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 release 依赖里的能力名规范成索引用的 id，依赖区间不动。"""
    for entry in releases:
        dependencies = entry.get("dependencies")
        if not isinstance(dependencies, list):
            continue
        for dependency in dependencies:
            if isinstance(dependency, dict) and "id" in dependency:
                dependency["id"] = current_capability_name(dependency["id"])
    return releases


def compat_capabilities(packages: dict[str, dict]) -> dict[str, int]:
    """能力 id -> 版本段数：`sp-compat` 块按它判断通配与精确写法够不够段数。

    游戏能力是 4 段；加载器提供的能力按 3 段。
    """
    parts: dict[str, int] = {GAME_CAPABILITY_ID: GAME_VERSION_SEGMENTS}
    for package in packages.values():
        for capability_id in declared_capabilities(package):
            parts.setdefault(capability_id, LOADER_VERSION_SEGMENTS)
    return parts


class RegistryError(ValueError):
    pass


class CompatibilityError(ValueError):
    """某个 release 的 `sp-compat` 块按写下的样子没法用。"""


def _github_json(path: str, *, missing_ok: bool = False) -> Any:
    """GET a GitHub API path; `missing_ok` maps HTTP 404 to None."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "sprocket-mod-registry-indexer/1",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(
                Request(GITHUB_API_URL + path, headers=headers), timeout=30
            ) as response:
                return json.load(response)
        except HTTPError as exc:
            last_error = exc
            if missing_ok and exc.code == 404:
                return None
            if exc.code not in {429, 500, 502, 503, 504}:
                raise RegistryError(
                    f"GitHub API returned HTTP {exc.code} for {path}"
                ) from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(attempt + 1)
    raise RegistryError(f"GitHub API request failed for {path}: {last_error}") from last_error


def _compat_version(text: str, parts: int, strict: bool) -> tuple[int, ...]:
    """解析一段版本；`strict` 时要求写满段数。

    4 段及以上（游戏）必须写全：`0.2.53` 有歧义（少一段？还是整段通配？），拒绝比猜好。
    段数较少的轴（加载器 3 段）允许省略，`0.8` 按 `0.8.0` 补齐。
    """
    segments = text.strip().split(".")
    if not 1 <= len(segments) <= parts or not all(segment.isdigit() for segment in segments):
        raise CompatibilityError(f"{text!r} 不是合法版本（最多 {parts} 段数字）")
    if strict and len(segments) != parts:
        raise CompatibilityError(f"{text!r} 少了段数：要写满 {parts} 段，整段通配写成 x.y.z.x")
    return tuple(int(segment) for segment in segments) + (0,) * (parts - len(segments))


def _compat_shift(version: tuple[int, ...], delta: int) -> tuple[int, ...]:
    return (*version[:-1], version[-1] + delta)


def _compat_upper_for_caret(version: tuple[int, ...]) -> tuple[int, ...]:
    if version[0] > 0:
        return (version[0] + 1, *([0] * (len(version) - 1)))
    if version[1] > 0:
        return (0, version[1] + 1, *([0] * (len(version) - 2)))
    return (0, 0, version[2] + 1, *([0] * (len(version) - 3)))


def _compat_upper_for_tilde(version: tuple[int, ...]) -> tuple[int, ...]:
    return (version[0], version[1] + 1, *([0] * (len(version) - 2)))


def _compat_token_range(
        token: str,
        parts: int,
        strict: bool,
) -> tuple[tuple[int, ...] | None, tuple[int, ...] | None]:
    """单个 token -> (下界, 上界)；`^`/`~` 会展开成上下界，其余 token 只给一侧。"""
    match = re.fullmatch(r"(>=|<=|>|<|=|\^|~)?(.+)", token)
    if match is None:  # pragma: no cover - 正则保证不会发生
        raise CompatibilityError(f"无法解析的版本区间 token：{token!r}")
    operator = match.group(1) or "="
    target = match.group(2).strip()

    wildcard = re.fullmatch(r"(\d+(?:\.\d+)*)\.(?:x|X|\*)", target)
    if wildcard is not None:
        if operator != "=":
            raise CompatibilityError(f"通配不能带比较符：{token!r}")
        segments = wildcard.group(1).split(".")
        if len(segments) != parts - 1 or not all(segment.isdigit() for segment in segments):
            raise CompatibilityError(f"{token!r} 不是 {parts} 段版本的通配写法")
        numeric = tuple(int(segment) for segment in segments)
        successor = numeric[:-1] + (numeric[-1] + 1, 0)
        return numeric + (0,), _compat_shift(successor, -1)

    # 比较符两侧允许省略段数（`<0.2.54` 明确就是「0.2.54.0 之前」）；
    # 写成精确值/用 ^ ~ 时才要求写满，因为 `0.2.53` 到底是哪一段有歧义。
    exact = operator in {"=", "^", "~"}
    version = _compat_version(target, parts, strict and exact)
    if operator == ">=":
        return version, None
    if operator == ">":
        return _compat_shift(version, 1), None
    if operator == "<=":
        return None, version
    if operator == "<":
        return None, _compat_shift(version, -1)
    if operator == "^":
        return version, _compat_shift(_compat_upper_for_caret(version), -1)
    if operator == "~":
        return version, _compat_shift(_compat_upper_for_tilde(version), -1)
    return version, version


def _compat_item_range(
        item: str,
        parts: int,
        strict: bool,
) -> tuple[tuple[int, ...] | None, tuple[int, ...] | None]:
    """一个作者写的区间项 -> 闭区间上下界。

    空格＝并且，但一个项里**最多一个下界 + 一个上界**（够覆盖所有正常写法；复杂布尔式不在这里猜）。
    末段为负数表示「恰好在它的后继之前」，这是 `<0.2.54.0` 或通配的上边界在合并过程中的表示方式
    （纯元组比较依然成立，输出时再还原成 `<...`）。
    """
    text = item.strip()
    if not text:
        raise CompatibilityError("版本区间项不能为空")
    if text in {"*", "x", "X"}:
        return None, None

    hyphen = re.fullmatch(r"(\S+)\s+-\s+(\S+)", text)
    if hyphen:
        lower = _compat_version(hyphen.group(1), parts, strict)
        upper = _compat_version(hyphen.group(2), parts, strict)
        return lower, upper

    lower: tuple[int, ...] | None = None
    upper: tuple[int, ...] | None = None
    for token in text.split():
        token_lower, token_upper = _compat_token_range(token, parts, strict)
        if token_lower is not None:
            if lower is not None:
                raise CompatibilityError(f"一个区间项里只能有一个下界：{text!r}")
            lower = token_lower
        if token_upper is not None:
            if upper is not None:
                raise CompatibilityError(f"一个区间项里只能有一个上界：{text!r}")
            upper = token_upper
    return lower, upper


def _compat_bound_text(version: tuple[int, ...], *, lower: bool) -> str:
    if version[-1] < 0:
        exclusive = _compat_shift(version, 1)
        return (">" if lower else "<") + ".".join(str(part) for part in exclusive)
    return (">=" if lower else "<=") + ".".join(str(part) for part in version)


def _compat_numbered_range(items: list[str], parts: int) -> str:
    """数字段写法 -> 一条 `>=a <=b`（多段用 `||`）：合并重叠与相接的段，按段号排序。"""
    # 4 段及以上必须写全段数（少一段有歧义）；段数少的轴允许省略（0.8 按 0.8.0 补齐）
    strict = parts >= 4
    ranges = [_compat_item_range(item, parts, strict) for item in items]
    for lower, upper in ranges:
        if lower is not None and upper is not None and upper < lower:
            raise CompatibilityError(
                f"区间上界低于下界：{'.'.join(map(str, lower))} > {'.'.join(map(str, upper))}"
            )

    ordered = sorted(
        ranges,
        key=lambda bounds: (bounds[0] is not None, bounds[0] if bounds[0] is not None else ()),
    )
    merged: list[list[tuple[int, ...] | None]] = []
    for lower, upper in ordered:
        if merged:
            last_upper = merged[-1][1]
            overlaps = last_upper is None or (lower is not None and lower <= _compat_shift(last_upper, 1))
            if overlaps:
                merged[-1][1] = None if (last_upper is None or upper is None) else max(last_upper, upper)
                continue
        merged.append([lower, upper])

    if len(merged) == 1 and merged[0][0] is None and merged[0][1] is None:
        return "*"

    clauses: list[str] = []
    for lower, upper in merged:
        if lower is None:
            clauses.append(_compat_bound_text(upper, lower=False))  # type: ignore[arg-type]
        elif upper is None:
            clauses.append(_compat_bound_text(lower, lower=True))
        else:
            clauses.append(
                f"{_compat_bound_text(lower, lower=True)} {_compat_bound_text(upper, lower=False)}"
            )
    return " || ".join(clauses)


def _compat_is_prerelease(item: str) -> bool:
    """这一项带预发布段（`6.0.0-be.788`）：它不是整数区间，不能按段号合并。"""
    return "-" in item


def canonical_compat_range(items: list[str], parts: int) -> str:
    """把作者写的一组区间项规范成一条 `>=a <=b`（多段用 `||` 连接）。

    带预发布段的项按 `domain.semver` 校验后原样保留成单独一段：加载器版本就长这样
    （`6.0.0-be.788`），后缀本身就是版本的一部分，砍掉就把两个构建说成同一个了。
    """
    prerelease = [item.strip() for item in items if _compat_is_prerelease(item)]
    numbered = [item for item in items if not _compat_is_prerelease(item)]

    clauses: list[str] = [_compat_numbered_range(numbered, parts)] if numbered else []
    for item in prerelease:
        try:
            validate_range(item)
        except ValueError as exc:
            raise CompatibilityError(f"{item!r} 不是合法版本区间：{exc}") from exc
        clauses.append(item)
    return " || ".join(clauses)


def compat_ranges(
        payload: dict[str, Any],
        capabilities: dict[str, int],
) -> tuple[dict[str, str], list[str]]:
    """`{能力 id: 区间字符串或它们的列表}` -> `{能力 id: 规范化区间}`，外加未知能力的告警。

    键先规范化：发布说明里可能用的是简称键名（`sprocket` / `melonloader`），否则一个还能用的
    声明会被当成未知轴丢掉。
    """
    payload = {current_capability_name(key): value for key, value in payload.items()}

    warnings: list[str] = []
    unknown = sorted(set(payload) - set(capabilities))
    if unknown:
        warnings.append("已忽略未知的能力：" + ", ".join(unknown))

    declared: dict[str, str] = {}
    for capability_id, parts in capabilities.items():
        raw = payload.get(capability_id)
        if raw is None:
            continue
        items = [raw] if isinstance(raw, str) else raw
        if not isinstance(items, list) or not items or not all(isinstance(item, str) for item in items):
            raise CompatibilityError(f"{capability_id} 必须是版本区间字符串或它们的非空列表")
        declared[capability_id] = canonical_compat_range(list(items), parts)
    return declared, warnings


def parse_compat_block(body: object, capabilities: dict[str, int]) -> tuple[dict[str, str], list[str]]:
    """发布说明里的 `sp-compat` 块 -> {能力 id: 规范化区间}，外加（非致命的）写法告警。

    没有块＝这条 release 没有声明（返回空，不报错）。块在但内容没法用 -> CompatibilityError，
    调用方把该 release 记成「未声明」，其他 release 不受影响。
    """
    match = COMPAT_BLOCK_RE.search(str(body or ""))
    if match is None:
        return {}, []
    try:
        payload = json.loads(match.group("body"))
    except json.JSONDecodeError as exc:
        raise CompatibilityError(f"sp-compat 块不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise CompatibilityError("sp-compat 块必须是一个 JSON 对象")
    return compat_ranges(payload, capabilities)


def _capability_dependencies(
        declared: dict[str, str],
        capabilities: dict[str, int],
) -> list[dict[str, str]]:
    return [
        {"id": capability_id, "version": declared[capability_id]}
        for capability_id in capabilities
        if capability_id in declared
    ]


def apply_release_compatibility(
        releases: list[dict[str, Any]],
        capabilities: dict[str, int],
        overrides: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """把兼容声明落成对能力的依赖，并处理继承。

    从旧到新走一遍：自己有可用声明就用自己那份；没有就沿用比它旧、最近一个有可用声明的
    release（`compatibility.from_tag` 始终指向**最初声明**的那个 tag，继承链不会越接越长）；
    写坏的声明不继承，只留告警。正文没写（或写坏）`sp-compat` 的 release 先看 `overrides`
    （这个包的覆盖条目，先命中的优先），命中的那条说的是哪个版本段就是哪个 —— 它比继承优先，
    否则一个包里只有最早命中的那条生效。补上的那份和作者写的一样成为后面 release 的继承来源。
    """
    ordered = sorted(releases, key=lambda entry: _release_version(entry) or Version.parse("0.0.0"))
    carried_dependencies: list[dict[str, str]] | None = None
    carried_origin = ""

    for entry in ordered:
        declared = entry.pop("compat_declared", None) or {}
        warnings = list(entry.pop("compat_warnings", None) or [])
        invalid = bool(entry.pop("compat_invalid", False))
        tag = str(entry.get("tag", ""))
        # 基线里已经解析过的条目直接带着依赖过来：能力名也要走一遍规范化。
        migrate_release_dependencies([entry])
        compatibility: dict[str, Any] = {}

        # 基线里自己声明过的条目（`source: declared`，正文或上一轮的覆盖表写的）以它为准；
        # 只有继承来的那份才让位给覆盖表。
        source = str((entry.get("compatibility") or {}).get("source"))
        version = _release_version(entry)
        if not declared and version is not None and source != "declared":
            matched = match_compat_override(overrides or [], version)
            if matched is not None:
                # 上一遍落在条目上的告警（正文写坏）不能跟着被替换掉的区间一起丢。
                stored = list((entry.get("compatibility") or {}).get("warnings") or [])
                warnings = stored or warnings
                entry.pop("dependencies", None)
                entry.pop("compatibility", None)
                declared = matched

        if declared:
            dependencies = _capability_dependencies(declared, capabilities)
            entry["dependencies"] = dependencies
            compatibility = {"source": "declared"}
            carried_dependencies = dependencies
            carried_origin = tag
        elif entry.get("dependencies"):
            # 增量基线里已经解析过的条目：原样保留，并且仍然可以作为继承来源
            carried_dependencies = [dict(item) for item in entry["dependencies"]]
            compatibility = dict(entry.get("compatibility") or {})
            carried_origin = str(compatibility.get("from_tag") or tag)
        elif carried_dependencies is not None and not invalid:
            entry["dependencies"] = [dict(item) for item in carried_dependencies]
            compatibility = {"source": "inherited", "from_tag": carried_origin}

        if warnings:
            compatibility["warnings"] = warnings
        if compatibility:
            entry["compatibility"] = compatibility

    return releases


def _package_compatibility_warnings(releases: list[dict[str, Any]]) -> list[str]:
    collected: list[str] = []
    for entry in releases:
        tag = str(entry.get("tag", "")) or "?"
        for message in (entry.get("compatibility") or {}).get("warnings", []) or []:
            collected.append(f"{tag}: {message}")
    return collected


def match_compat_override(
        entries: list[dict[str, Any]],
        version: Version,
) -> dict[str, str] | None:
    """这条 release 版本命中的第一条覆盖声明；一个包里先写的条目优先。"""
    for entry in entries:
        if satisfies(version, entry["version"]):
            return dict(entry["compat"])
    return None


def unmatched_compat_overrides(
        entries: list[dict[str, Any]],
        releases: list[dict[str, Any]],
) -> list[str]:
    """没有对上这个包任何 release 版本的覆盖条目：区间写错或者包换错了才会这样。"""
    versions = [version for version in map(_release_version, releases) if version is not None]
    return [
        entry["version"]
        for entry in entries
        if not any(satisfies(version, entry["version"]) for version in versions)
    ]


def load_compat_overrides(
        path: Path,
        package_ids: set[str] | None = None,
        capabilities: dict[str, int] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """读兼容声明覆盖表：`{包 id: [{version, compat}]}`，一个包里的条目按文件顺序分优先级。

    返回 (表, 告警)。文件不存在、或者条目是空列表，都是没有这张表的意思；某一条写坏只跳过
    那一条并留告警，不影响其他条目，也不影响索引生成。给定了 `package_ids` 时，`id` 必须是
    注册表里某个包 —— 否则这条覆盖谁也认不出来；`capabilities` 给出能力轴与各自的段数，
    不给就只有游戏轴。`version` 是包自己 release 版本的区间，省略表示这个包的全部 release。
    """
    warnings: list[str] = []
    if not path.is_file():
        return {}, warnings
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"{path.name}: 无法读取（{exc}）"]

    if not isinstance(raw, dict):
        return {}, [f"{path.name}: 顶层必须是对象"]
    unknown = sorted(set(raw) - {"schema_version", "entries"})
    if unknown:
        warnings.append(f"{path.name}: 忽略了未知键 {', '.join(unknown)}")
    entries = raw.get("entries")
    if not isinstance(entries, list):
        warnings.append(f"{path.name}: entries 必须是列表")
        return {}, warnings

    parts = capabilities if capabilities is not None else {GAME_CAPABILITY_ID: GAME_VERSION_SEGMENTS}
    table: dict[str, list[dict[str, Any]]] = {}
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            warnings.append(f"{path.name} 第 {position} 条：必须是对象")
            continue
        missing = sorted({"id", "compat"} - set(entry))
        if missing:
            warnings.append(f"{path.name} 第 {position} 条：缺少 {', '.join(missing)}")
            continue
        package_id = str(entry["id"])
        if package_ids is not None and package_id not in package_ids:
            warnings.append(f"{path.name} 第 {position} 条：未知的包 {package_id}")
            continue
        version = entry.get("version", "*")
        if not isinstance(version, str):
            warnings.append(f"{path.name} 第 {position} 条：version 必须是区间字符串")
            continue
        try:
            validate_range(version)
        except ValueError as exc:
            warnings.append(f"{path.name} 第 {position} 条：{exc}")
            continue
        compat = entry["compat"]
        if not isinstance(compat, dict) or not compat:
            warnings.append(f"{path.name} 第 {position} 条：compat 必须是非空对象")
            continue
        try:
            ranges, compat_warnings = compat_ranges(compat, parts)
        except CompatibilityError as exc:
            warnings.append(f"{path.name} 第 {position} 条：{exc}")
            continue
        warnings.extend(f"{path.name} 第 {position} 条：{message}" for message in compat_warnings)
        if not ranges:
            warnings.append(f"{path.name} 第 {position} 条：compat 里没有可用的能力轴")
            continue
        table.setdefault(package_id, []).append({"version": version, "compat": ranges})
    return table, warnings


def normalize_release_records(
        package: dict[str, Any],
        records: object,
        capabilities: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    capabilities = (
        capabilities if capabilities is not None else {GAME_CAPABILITY_ID: GAME_VERSION_SEGMENTS}
    )
    if not isinstance(records, list):
        raise RegistryError(f"{package['id']}: GitHub Releases response is not a list")
    release_rules = package["release"]
    version_pattern = re.compile(release_rules["version_pattern"])
    include_prerelease = release_rules["include_prerelease"]
    includes = tuple(pattern.casefold() for pattern in release_rules["assets"]["include"])
    excludes = tuple(pattern.casefold() for pattern in release_rules["assets"]["exclude"])
    expected_download_prefix = f"/{package['repository']}/releases/download/".casefold()
    expected_page_prefix = f"/{package['repository']}/releases/tag/".casefold()
    releases: list[tuple[Version, dict[str, Any]]] = []

    for record in records:
        if not isinstance(record, dict):
            continue
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
        page_url = str(record.get("html_url", ""))
        parsed_page = urlparse(page_url)
        if (
            parsed_page.scheme != "https"
            or (parsed_page.hostname or "").casefold() != "github.com"
            or not parsed_page.path.casefold().startswith(expected_page_prefix)
        ):
            continue

        assets: list[dict[str, Any]] = []
        for asset in record.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name", ""))
            folded_name = name.casefold()
            if Path(name).suffix.casefold() not in INSTALLABLE_SUFFIXES:
                continue
            if not any(fnmatch.fnmatchcase(folded_name, pattern) for pattern in includes):
                continue
            if any(fnmatch.fnmatchcase(folded_name, pattern) for pattern in excludes):
                continue
            download_url = str(asset.get("browser_download_url", ""))
            parsed_download = urlparse(download_url)
            if (
                parsed_download.scheme != "https"
                or (parsed_download.hostname or "").casefold() != "github.com"
                or not parsed_download.path.casefold().startswith(expected_download_prefix)
            ):
                continue
            assets.append(
                {
                    "id": int(asset.get("id", 0)),
                    "name": name,
                    "size": int(asset.get("size", 0)),
                    "download_url": download_url,
                    "digest": asset.get("digest") or None,
                    "updated_at": str(asset.get("updated_at", "")),
                }
            )
        if not assets:
            continue
        try:
            declared, compat_warnings = parse_compat_block(record.get("body"), capabilities)
            compat_invalid = False
        except CompatibilityError as exc:
            declared, compat_warnings, compat_invalid = {}, [str(exc)], True
        releases.append(
            (
                version,
                {
                    "id": int(record.get("id", 0)),
                    "tag": tag,
                    "version": str(version),
                    "prerelease": bool(record.get("prerelease")),
                    "published_at": str(record.get("published_at", "")),
                    "page_url": page_url,
                    "assets": assets,
                    # 下面三个是内部字段，`apply_release_compatibility` 用完会摘掉
                    "compat_declared": declared,
                    "compat_warnings": compat_warnings,
                    "compat_invalid": compat_invalid,
                },
            )
        )

    releases.sort(key=lambda item: item[0], reverse=True)
    normalized = [record for _version, record in releases]
    if not normalized:
        raise RegistryError(f"{package['id']}: no compatible GitHub Release asset")
    return normalized


def _encoded_repository(package: dict[str, Any]) -> str:
    return "/".join(quote(part, safe="") for part in package["repository"].split("/", 1))


def _release_version(entry: dict[str, Any]) -> Version | None:
    """Version of one normalized release; None when unreadable (it is kept, just ordered last)."""
    try:
        return Version.parse(str(entry.get("version", "")))
    except (IndexError, ValueError):
        return None


def _release_key(entry: dict[str, Any]) -> str:
    release_id = entry.get("id")
    return f"id:{release_id}" if release_id else f"tag:{entry.get('tag')}"


def sort_releases_desc(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def order(entry: dict[str, Any]) -> tuple[int, Version, str]:
        version = _release_version(entry)
        return (
            1 if version is not None else 0,
            version or Version.parse("0.0.0"),
            str(entry.get("published_at", "")),
        )

    return sorted(entries, key=order, reverse=True)


def newest_known_release(known: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Highest release among the ones we already hold; the probe is compared against it."""
    newest: dict[str, Any] | None = None
    newest_version: Version | None = None
    for entry in known:
        version = _release_version(entry)
        if version is None:
            continue
        if newest_version is None or version > newest_version:
            newest, newest_version = entry, version
    return newest if newest is not None else (known[0] if known else None)


def latest_release_record(package: dict[str, Any]) -> dict[str, Any] | None:
    """Raw `/releases/latest` record, used to tell whether the newest release changed.

    Only meaningful for packages that skip prereleases: GitHub's latest endpoint never
    returns one, so for `include_prerelease` packages it cannot stand for the newest
    release and this returns None (those packages always read the list). A repository
    without releases answers 404, which counts as "nothing there".
    """
    if package["release"].get("include_prerelease"):
        return None
    record = _github_json(f"/repos/{_encoded_repository(package)}/releases/latest", missing_ok=True)
    return record if isinstance(record, dict) else None


def is_same_release(probe: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Whether the probed release is the one we already hold (same release id or tag)."""
    probe_id = probe.get("id")
    if isinstance(probe_id, int) and probe_id and probe_id == entry.get("id"):
        return True
    tag = str(probe.get("tag_name", ""))
    return bool(tag) and tag == str(entry.get("tag", ""))


def merge_releases(
        known: list[dict[str, Any]],
        fetched: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge freshly fetched releases into the ones already known.

    Everything at or above the oldest version the fetch covered comes from the fetch —
    a release deleted upstream disappears with it. Anything older is kept as it is,
    so older releases are neither re-fetched nor re-normalized.
    """
    if not fetched:
        return list(known)
    if not known:
        return sort_releases_desc(list(fetched))

    fetched_versions = [
        version for version in (_release_version(entry) for entry in fetched) if version is not None
    ]
    oldest_fetched = min(fetched_versions) if fetched_versions else None
    kept: list[dict[str, Any]] = []
    for entry in known:
        version = _release_version(entry)
        if oldest_fetched is None or version is None or version < oldest_fetched:
            kept.append(entry)

    merged: dict[str, dict[str, Any]] = {_release_key(entry): entry for entry in kept}
    for entry in fetched:
        merged[_release_key(entry)] = entry
    return sort_releases_desc(list(merged.values()))


def fetch_package_releases(
        package: dict[str, Any],
        known: list[dict[str, Any]] | None = None,
        capabilities: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Read a package's releases, folding only the *new* ones into `known`.

    A small `/releases/latest` probe first: while the newest release is still the one we
    hold, the previous data is returned as it is and no release list is read at all.
    When it did change, the list is read and only the unseen releases are merged in.
    """
    previous = list(known or [])
    resolved_capabilities = (
        capabilities if capabilities is not None
        else {GAME_CAPABILITY_ID: GAME_VERSION_SEGMENTS}
    )
    if previous:
        newest = newest_known_release(previous)
        probe = latest_release_record(package)
        if probe is not None and newest is not None and is_same_release(probe, newest):
            # 最新版没变，直接沿用基线那份；基线里的能力名也要走一遍规范化。
            return migrate_release_dependencies(previous)

    encoded = _encoded_repository(package)
    records = _github_json(f"/repos/{encoded}/releases?per_page=100")
    try:
        normalized = normalize_release_records(package, records, resolved_capabilities)
    except RegistryError as list_error:
        latest = _github_json(f"/repos/{encoded}/releases/latest", missing_ok=True)
        if not isinstance(latest, dict):
            raise list_error
        try:
            normalized = normalize_release_records(package, [latest], resolved_capabilities)
        except RegistryError:
            raise list_error
    return apply_release_compatibility(
        merge_releases(previous, normalized), resolved_capabilities
    )


def validate_target(target: str) -> None:
    if not target or "\\" in target or ":" in target:
        raise RegistryError(f"unsafe install target: {target!r}")
    path = PurePosixPath(target)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RegistryError(f"unsafe install target: {target!r}")
    if path.parts[0] not in ALLOWED_TARGET_ROOTS:
        raise RegistryError(f"target root is not allowed: {target!r}")


def validate_localized(value: object, field: str) -> None:
    if not isinstance(value, dict) or not value:
        raise RegistryError(f"{field} must be a non-empty localized object")
    seen_languages: set[str] = set()
    for language, text in value.items():
        if not isinstance(language, str) or not LANGUAGE_TAG_RE.fullmatch(language):
            raise RegistryError(f"{field} has an invalid language tag: {language!r}")
        folded_language = language.casefold()
        if folded_language in seen_languages:
            raise RegistryError(f"{field} contains a duplicate language tag: {language!r}")
        seen_languages.add(folded_language)
        if not isinstance(text, str) or not text.strip():
            raise RegistryError(f"{field} values must be non-empty strings")
        if len(text) > 1000:
            raise RegistryError(f"{field} values must not exceed 1000 characters")


def release_source_type(package: dict[str, Any]) -> str:
    """条目的二进制来源：默认是它自己的 GitHub Releases。"""
    release = package.get("release")
    source = release.get("source") if isinstance(release, dict) else None
    if isinstance(source, dict) and isinstance(source.get("type"), str):
        return source["type"]
    return "github"


def validate_file_rules(rules: object) -> None:
    if not isinstance(rules, list):
        raise RegistryError("install.files must be a list")
    for rule in rules:
        if not isinstance(rule, dict):
            raise RegistryError("install file rules must be objects")
        if not {"match", "type"} <= set(rule):
            raise RegistryError("install file rule requires match and type")
        if not isinstance(rule["match"], str) or not rule["match"]:
            raise RegistryError("install file rule match must be a non-empty string")
        if not isinstance(rule["type"], str) or not (
            FILE_TYPE_RE.fullmatch(rule["type"]) or FILE_TYPE_WILDCARD_RE.fullmatch(rule["type"])
        ):
            raise RegistryError(f"invalid install file type: {rule['type']!r}")
        if "subpath" in rule and (
            not isinstance(rule["subpath"], str) or not SUBPATH_RE.fullmatch(rule["subpath"])
        ):
            raise RegistryError(f"invalid install subpath: {rule['subpath']!r}")
        if "layout" in rule and rule["layout"] not in {"file", "tree"}:
            raise RegistryError(f"invalid install layout: {rule['layout']!r}")


def validate_payload_rules(rules: object) -> None:
    """加载器自己的安装线：目标直接是游戏根目录下的位置，不经过供给表。"""
    if not isinstance(rules, list):
        raise RegistryError("install.payload must be a list")
    for rule in rules:
        if not isinstance(rule, dict):
            raise RegistryError("install payload rules must be objects")
        if not {"match", "target"} <= set(rule):
            raise RegistryError("install payload rule requires match and target")
        if not isinstance(rule["match"], str) or not rule["match"]:
            raise RegistryError("install payload rule match must be a non-empty string")
        if not isinstance(rule["target"], str) or not SUPPLY_TARGET_RE.fullmatch(rule["target"]):
            raise RegistryError(f"invalid install payload target: {rule['target']!r}")
        if "subpath" in rule and (
            not isinstance(rule["subpath"], str) or not SUBPATH_RE.fullmatch(rule["subpath"])
        ):
            raise RegistryError(f"invalid install subpath: {rule['subpath']!r}")
        if "layout" in rule and rule["layout"] not in {"file", "tree"}:
            raise RegistryError(f"invalid install layout: {rule['layout']!r}")


def validate_replace_types(install: dict[str, Any], mode: str) -> None:
    """`install.replace`：整体接管某个类型的供给目录，只跟 `mode: patch` 一起用。

    接管一个类型就是安装时清空那个目录再落文件，所以这个包自己的 `files` 规则必须真的用
    那个类型 —— 否则接管的目录和它装的文件对不上。
    """
    raw = install.get("replace")
    if raw is None:
        return
    if mode != PATCH_MODE:
        raise RegistryError("install.replace requires install.mode patch")
    if not isinstance(raw, list) or not raw or len(raw) != len(set(raw)):
        raise RegistryError("install.replace must be a non-empty list without duplicates")
    file_types = {
        str(rule["type"]) for rule in install.get("files", ()) if isinstance(rule, dict)
    }
    for file_type in raw:
        if not isinstance(file_type, str) or not FILE_TYPE_RE.fullmatch(file_type):
            raise RegistryError(f"invalid replaced type: {file_type!r}")
        if file_type not in file_types:
            raise RegistryError(f"install.replace needs a files rule for {file_type}")


def validate_provides(meta: dict[str, Any]) -> None:
    """`provides`：能力 id -> 版本字符串；`{version}` 表示这条发布自己的版本。"""
    if "provides" not in meta:
        return
    provides = meta["provides"]
    if not isinstance(provides, dict) or not provides:
        raise RegistryError("provides must be a non-empty object")
    for capability_id, version in provides.items():
        if not isinstance(capability_id, str) or not ID_RE.fullmatch(capability_id):
            raise RegistryError(f"invalid provided capability id: {capability_id!r}")
        if not isinstance(version, str) or not version:
            raise RegistryError(f"provided capability {capability_id} needs a version")
        if version != VERSION_TEMPLATE:
            try:
                Version.parse(version)
            except (IndexError, ValueError) as exc:
                raise RegistryError(
                    f"provided capability {capability_id} has an invalid version: {version!r}"
                ) from exc


def validate_release_records(meta: dict[str, Any]) -> None:
    """条目自带的发布记录：每条要有 `id`、`version`、`assets`，每条资产要有 `id` 与 `download_url`。

    这几项是客户端定位版本、下载与校验所依赖的最小集合；`tag`、`page_url`、`digest`
    这类字段由条目自己决定写不写。
    """
    package_id = meta.get("id")
    records = meta.get("releases")
    if not isinstance(records, list) or not records:
        raise RegistryError("an external release source needs the entry to carry its releases")
    for record in records:
        if not isinstance(record, dict):
            raise RegistryError(f"{package_id}: release records must be objects")
        for field in ("id", "version", "assets"):
            if field not in record:
                raise RegistryError(f"{package_id}: release record is missing {field}")
        if not isinstance(record["id"], int) or isinstance(record["id"], bool):
            raise RegistryError(f"{package_id}: release id must be an integer")
        version = record["version"]
        if not isinstance(version, str) or not version:
            raise RegistryError(f"{package_id}: release version must be a non-empty string")
        try:
            Version.parse(version)
        except (IndexError, ValueError) as exc:
            raise RegistryError(f"{package_id}: release version is not SemVer: {version!r}") from exc
        assets = record["assets"]
        if not isinstance(assets, list):
            raise RegistryError(f"{package_id}: release assets must be a list")
        for asset in assets:
            if not isinstance(asset, dict):
                raise RegistryError(f"{package_id}: release assets must be objects")
            for field in ("id", "download_url"):
                if field not in asset:
                    raise RegistryError(f"{package_id}: release asset is missing {field}")
            if not isinstance(asset["id"], int) or isinstance(asset["id"], bool):
                raise RegistryError(f"{package_id}: release asset id must be an integer")
            if not isinstance(asset["download_url"], str) or not asset["download_url"]:
                raise RegistryError(
                    f"{package_id}: release asset download_url must be a non-empty string"
                )


def validate_meta(meta: dict, expected_id: str) -> None:
    """必填只有 schema_version、id、name、install，其余字段写了才校验形状。

    未定义的键原样进索引：条目是作者的文件，索引不替它筛字段。
    """
    missing = sorted(REQUIRED_FIELDS - set(meta))
    if missing:
        raise RegistryError(f"missing fields: {', '.join(missing)}")

    forbidden = sorted(FORBIDDEN_VERSION_FIELDS & set(meta))
    if forbidden:
        raise RegistryError(
            "Pages metadata must not contain release versions: " + ", ".join(forbidden)
        )

    schema_version = meta.get("schema_version")
    if schema_version not in (1, 2, 3):
        raise RegistryError("schema_version must be 1, 2, or 3")
    package_id = meta.get("id", "")
    if not ID_RE.fullmatch(package_id):
        raise RegistryError(f"invalid package id: {package_id!r}")
    if package_id != expected_id:
        raise RegistryError(f"id {package_id!r} does not match its file path (expected {expected_id!r})")
    if "repository" in meta and not REPOSITORY_RE.fullmatch(str(meta["repository"])):
        raise RegistryError(f"invalid GitHub repository: {meta['repository']!r}")
    if "license" in meta and not meta["license"]:
        raise RegistryError("license must be a non-empty string")
    if "authors" in meta:
        authors = meta["authors"]
        if not isinstance(authors, list) or not authors:
            raise RegistryError("authors must be a non-empty list")
        if not all(isinstance(author, str) and author.strip() for author in authors):
            raise RegistryError("authors must contain non-empty strings")
    if "display_name" in meta:
        validate_localized(meta["display_name"], "display_name")
    if "description" in meta:
        validate_localized(meta["description"], "description")
    if "category" in meta and meta["category"] not in ALLOWED_CATEGORIES:
        raise RegistryError(f"invalid category: {meta.get('category')!r}")
    if "tags" in meta:
        tags = meta["tags"]
        if not isinstance(tags, list) or len(tags) != len(set(tags)):
            raise RegistryError("tags must be a list without duplicates")
        if not all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", tag or "") for tag in tags):
            raise RegistryError("tags must use lowercase letters, digits, and hyphens")

    kind = str(meta.get("kind", "modfile"))
    if kind not in PACKAGE_KINDS:
        raise RegistryError(f"invalid kind: {meta.get('kind')!r}")
    validate_provides(meta)

    if "release" in meta:
        release = meta["release"]
        if not isinstance(release, dict):
            raise RegistryError("release must be an object")
        if "include_prerelease" in release and not isinstance(release["include_prerelease"], bool):
            raise RegistryError("release.include_prerelease must be a boolean")
        if "version_pattern" in release:
            pattern = release["version_pattern"]
            if not isinstance(pattern, str) or not pattern:
                raise RegistryError("release.version_pattern must be a non-empty string")
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise RegistryError(f"invalid version_pattern: {exc}") from exc
            if compiled.groups < 1:
                raise RegistryError("version_pattern must contain a capture group for SemVer")
        if "assets" in release:
            assets = release["assets"]
            if not isinstance(assets, dict):
                raise RegistryError("release.assets must be an object")
            if "include" in assets and (
                not isinstance(assets["include"], list) or not assets["include"]
            ):
                raise RegistryError("release.assets.include must be a non-empty list")
            if "exclude" in assets and not isinstance(assets["exclude"], list):
                raise RegistryError("release.assets.exclude must be a list")
            patterns = list(assets.get("include") or []) + list(assets.get("exclude") or [])
            if not all(isinstance(item, str) and item for item in patterns):
                raise RegistryError("release asset patterns must be non-empty strings")

        source = release.get("source")
        if source is not None:
            if not isinstance(source, dict) or not isinstance(source.get("type"), str):
                raise RegistryError("release.source must be an object naming a type")
            if source["type"] not in {"github", "external"}:
                raise RegistryError(f"invalid release source type: {source['type']!r}")
            if source["type"] == "external":
                if kind != "modloader":
                    raise RegistryError("only a modloader may declare an external release source")
                hosts = source.get("hosts")
                if not isinstance(hosts, list) or not hosts or len(hosts) != len(set(hosts)):
                    raise RegistryError("an external release source needs a non-empty host list")
                if not all(isinstance(host, str) and HOST_RE.fullmatch(host) for host in hosts):
                    raise RegistryError("external release source hosts must be lowercase host names")
                validate_release_records(meta)
    if "releases" in meta and not (
        isinstance(meta.get("release"), dict)
        and isinstance(meta["release"].get("source"), dict)
        and meta["release"]["source"].get("type") == "external"
    ):
        raise RegistryError("releases only applies to an external release source")

    dependencies = meta.get("dependencies", [])
    if not isinstance(dependencies, list):
        raise RegistryError("dependencies must be a list")
    seen_rules: set[tuple[str, str]] = set()
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise RegistryError("dependency entries must be objects")
        missing_rule = sorted({"id", "version", "when"} - set(dependency))
        if missing_rule:
            raise RegistryError(f"dependency is missing {', '.join(missing_rule)}")
        key = (dependency["id"], dependency["when"])
        if key in seen_rules:
            raise RegistryError(f"duplicate dependency rule: {key[0]} when {key[1]}")
        seen_rules.add(key)
        if not ID_RE.fullmatch(dependency["id"]):
            raise RegistryError(f"invalid dependency id: {dependency['id']!r}")
        for field in ("version", "when"):
            try:
                validate_range(dependency[field])
            except ValueError as exc:
                raise RegistryError(f"invalid dependency {field}: {dependency[field]!r}") from exc

    recommendations = meta.get("recommendations", [])
    if not isinstance(recommendations, list):
        raise RegistryError("recommendations must be a list")
    if not all(isinstance(item, str) for item in recommendations):
        raise RegistryError("recommendations must contain package ids")
    if len(recommendations) != len(set(recommendations)):
        raise RegistryError("recommendations must not contain duplicates")
    for recommendation in recommendations:
        if not isinstance(recommendation, str) or not ID_RE.fullmatch(recommendation):
            raise RegistryError(f"invalid recommendation id: {recommendation!r}")
        if recommendation == package_id:
            raise RegistryError("package cannot recommend itself")

    if not isinstance(meta.get("featured", False), bool):
        raise RegistryError("featured must be a boolean")
    install = meta.get("install")
    if not isinstance(install, dict):
        raise RegistryError("install must be an object")
    if "exclude" in install and not isinstance(install["exclude"], list):
        raise RegistryError("install.exclude must be a list")
    mode = install.get("mode", "standard")
    if mode not in {"standard", PATCH_MODE}:
        raise RegistryError(f"invalid install mode: {mode!r}")

    if schema_version == 1:
        if "scan_dlls" in install and not isinstance(install["scan_dlls"], bool):
            raise RegistryError("install.scan_dlls must be a boolean")
        if "overrides" in install:
            if not isinstance(install["overrides"], list):
                raise RegistryError("install.overrides must be a list")
            for override in install["overrides"]:
                if not isinstance(override, dict):
                    raise RegistryError("install overrides must be objects")
                if not {"match", "target"} <= set(override):
                    raise RegistryError("install override requires match and target")
                if not isinstance(override["match"], str) or not override["match"]:
                    raise RegistryError("install override match must be a non-empty string")
                validate_target(override["target"])
    else:
        if "overrides" in install:
            raise RegistryError("install.overrides belongs to schema_version 1; use install.files")
        uses_payload = "payload" in install
        uses_files = "files" in install
        if uses_payload and uses_files:
            raise RegistryError("a package must not mix install.files and install.payload")
        if kind == "modfile" and uses_payload:
            raise RegistryError("a modfile installs through install.files")
        if uses_payload:
            validate_payload_rules(install["payload"])
        else:
            if "scan_dlls" in install and not isinstance(install["scan_dlls"], bool):
                raise RegistryError("install.scan_dlls must be a boolean")
            if uses_files:
                validate_file_rules(install["files"])
            validate_replace_types(install, mode)

    supply = meta.get("supply")
    if "supply" in meta:
        if schema_version < 2:
            raise RegistryError("supply requires schema_version 2")
        if not isinstance(supply, dict) or not supply:
            raise RegistryError("supply must be a non-empty object")
        for file_type, target in supply.items():
            if (
                not isinstance(file_type, str)
                or not FILE_TYPE_RE.fullmatch(file_type)
            ):
                raise RegistryError(f"invalid supplied type: {file_type!r}")
            if not isinstance(target, str) or not SUPPLY_TARGET_RE.fullmatch(target):
                raise RegistryError(f"invalid supply target: {target!r}")

    if kind == "modloader" and not supply:
        raise RegistryError("a modloader must supply at least one install type")


def find_dependency_cycle(packages: dict[str, dict]) -> list[str] | None:
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(package_id: str) -> list[str] | None:
        if package_id in visiting:
            start = visiting.index(package_id)
            return visiting[start:] + [package_id]
        if package_id in visited:
            return None
        visiting.append(package_id)
        for dependency in packages[package_id].get("dependencies", []):
            dependency_id = str(dependency.get("id", ""))
            if dependency_id not in packages:
                # 能力不是包，装不成环。
                continue
            cycle = visit(dependency_id)
            if cycle:
                return cycle
        visiting.pop()
        visited.add(package_id)
        return None

    for package_id in packages:
        cycle = visit(package_id)
        if cycle:
            return cycle
    return None


def validate_install_types(packages: dict[str, dict]) -> None:
    """模组静态声明的安装类型必须有人供给。

    一个类型可以由多个加载器供给（例如原生加载器和把目录重新安家的桥接加载器），所以这里
    只要求「至少有一个供给者」；具体用哪一个由客户端按目标目录里已安装的加载器决定。
    通配类型 `<ns>:*` 只要有该名字空间下的供给者就算有着落 —— 具体类别由 DLL 的 PE 元数据定。
    类型写法与供给位置由 `validate_meta` 校验。
    """
    suppliers: set[str] = {
        file_type
        for package in packages.values()
        for file_type in (package.get("supply") or {})
    }
    namespaces = {file_type.split(":", 1)[0] for file_type in suppliers}
    for package_id, package in packages.items():
        install = package.get("install")
        rules = install.get("files", []) if isinstance(install, dict) else []
        for rule in rules:
            file_type = rule["type"]
            if file_type.endswith(":*"):
                if file_type[: -len(":*")] not in namespaces:
                    raise RegistryError(
                        f"{package_id}: install file type is not supplied by any modloader: {file_type}"
                    )
                continue
            if file_type not in suppliers:
                raise RegistryError(
                    f"{package_id}: install file type is not supplied by any modloader: {file_type}"
                )


def scan_mods(mods_dir: Path) -> list[dict]:
    if not mods_dir.is_dir():
        raise RegistryError(f"mods directory does not exist: {mods_dir}")

    packages: dict[str, dict] = {}
    for author_dir in sorted(path for path in mods_dir.iterdir() if path.is_dir()):
        # 路径本身就是身份：`mods/<作者>/<modid>.json` 读作 `<作者>.<modid>`，条目里的 `id` 必须一致。
        for meta_path in sorted(author_dir.glob("*.json")):
            package_id = f"{author_dir.name}.{meta_path.stem}"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RegistryError(f"{package_id}: cannot read metadata: {exc}") from exc

            validate_meta(meta, package_id)
            package = {key: value for key, value in meta.items() if key != "$schema"}
            package["meta_url"] = f"mods/{author_dir.name}/{meta_path.name}"
            packages[package["id"]] = package

    capabilities = set(compat_capabilities(packages))
    for package in packages.values():
        for dependency in package.get("dependencies", []):
            dependency_id = dependency["id"]
            if dependency_id not in packages and dependency_id not in capabilities:
                raise RegistryError(
                    f"{package['id']}: dependency is not registered: {dependency_id}"
                )
        for recommendation in package.get("recommendations", []):
            if recommendation not in packages:
                raise RegistryError(
                    f"{package['id']}: recommendation is not registered: {recommendation}"
                )

    validate_install_types(packages)

    cycle = find_dependency_cycle(packages)
    if cycle:
        raise RegistryError("dependency cycle: " + " -> ".join(cycle))

    return [packages[key] for key in sorted(packages)]


def _newest_release_version(package: dict[str, Any]) -> tuple[int, Version]:
    """这条读数的最新发布版本；读不出来就是 `(0, 0.0.0)`（排最后）。"""
    releases = package.get("releases") or []
    if not releases:
        return (0, Version(0, 0, 0))
    try:
        return (1, Version.parse(str(releases[0].get("version", ""))))
    except ValueError:
        return (0, Version(0, 0, 0))


def release_order(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """索引里的包按最新发布版本从新到旧排；读不出发布版本的排最后，同版本按 id。

    站点「按版本」排序直接用这份次序，不再自己解析版本号 —— 版本高低只有这一处口径。
    """
    ordered = sorted(packages, key=lambda package: str(package.get("id", "")))
    ordered.sort(key=_newest_release_version, reverse=True)
    return ordered


def load_index_releases(source: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Releases per package from an existing index: the incremental baseline, and also
    what a failed fetch falls back to. `source` is a local file or an index URL.
    """
    if isinstance(source, Path) or not urlparse(str(source)).scheme:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        request = Request(str(source), headers={"User-Agent": "sprocket-mod-registry-indexer/1"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("packages"), list):
        raise RegistryError("index has an invalid package list")

    releases_by_id: dict[str, list[dict[str, Any]]] = {}
    for package in payload["packages"]:
        if not isinstance(package, dict):
            continue
        package_id = package.get("id")
        releases = package.get("releases")
        if isinstance(package_id, str) and isinstance(releases, list):
            releases_by_id[package_id] = releases
    return releases_by_id


def _normalized_provider_version(text: str) -> str | None:
    """加载器版本区间：数值写法规范成 `>=a.b.c`；带预发布段的按 semver 原样保留。

    供给表里的加载器版本可能是 `6.0.0-be.785` 这种带预发布段的 SemVer，数值区间写法
    放不下它，所以这一支交给 semver 校验后原样使用。
    """
    value = text.strip() or "*"
    try:
        return canonical_compat_range([value], LOADER_VERSION_SEGMENTS)
    except CompatibilityError:
        if not PRERELEASE_VERSION_RE.search(value):
            return None
        try:
            validate_range(value)
        except ValueError:
            return None
        return value


def load_providers_table(
        path: Path,
        loader_ids: set[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """读「加载器包 ↔ 游戏」表：每条记「某个加载器包的某段版本能跑哪段游戏版本」。

    返回值是 (表, 告警)。文件不存在就是没有这张表；某一条写坏只跳过那一条并留告警，
    不影响其他条目，也不影响索引生成。给定了 `loader_ids` 时，`loader` 必须是注册表里
    某个加载器类包的 id —— 否则这条声明谁也认不出来；不给表示这一层不检查身份
    （只校验表写法本身）。
    """
    warnings: list[str] = []
    empty: dict[str, Any] = {"schema_version": 2, "entries": []}
    if not path.is_file():
        return empty, warnings
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return empty, [f"{path.name}: 无法读取（{exc}）"]

    if not isinstance(raw, dict):
        return empty, [f"{path.name}: 顶层必须是对象"]
    unknown = sorted(set(raw) - {"schema_version", "entries"})
    if unknown:
        warnings.append(f"{path.name}: 忽略了未知键 {', '.join(unknown)}")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        warnings.append(f"{path.name}: entries 必须是非空列表")
        return empty, warnings

    normalized: list[dict[str, str]] = []
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            warnings.append(f"{path.name} 第 {position} 条：必须是对象")
            continue
        missing = sorted({"loader", "version", "sprocket"} - set(entry))
        if missing:
            warnings.append(f"{path.name} 第 {position} 条：缺少 {', '.join(missing)}")
            continue
        loader = str(entry["loader"])
        if loader_ids is not None and loader not in loader_ids:
            warnings.append(f"{path.name} 第 {position} 条：未知的加载器 {loader}")
            continue
        sprocket_text = str(entry["sprocket"])
        try:
            sprocket = canonical_compat_range([sprocket_text], GAME_VERSION_SEGMENTS)
        except CompatibilityError as exc:
            warnings.append(f"{path.name} 第 {position} 条：{exc}")
            continue
        version = _normalized_provider_version(str(entry["version"]))
        if version is None:
            warnings.append(f"{path.name} 第 {position} 条：无法解析的加载器版本区间")
            continue
        normalized.append({"loader": loader, "version": version, "sprocket": sprocket})

    if not normalized:
        return empty, warnings
    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, int):
        schema_version = 2
    return {"schema_version": schema_version, "entries": normalized}, warnings


def _diagnosis_text_problems(value: Any, name: str) -> list[str]:
    """两种语言都要有：界面语言只有这两种，缺一种就会让另一种语言的使用者看到别的语言。"""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"{name} 必须是对象"]
    problems = [
        f"{name} 缺少 {language}"
        for language in ("zh", "en")
        if not isinstance(value.get(language), str) or not value[language].strip()
    ]
    return problems


def _diagnosis_tutorial_problems(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return ["tutorial 必须是对象"]
    problems = []
    for language in ("zh", "en"):
        steps = value.get(language)
        if isinstance(steps, str) and steps.strip():
            problems.append(f"tutorial.{language} 必须是步骤列表")
            continue
        if not isinstance(steps, list) or not steps:
            problems.append(f"tutorial 缺少 {language}")
            continue
        if not all(isinstance(step, str) and step.strip() for step in steps):
            problems.append(f"tutorial.{language} 里有空步骤")
    return problems


def _diagnosis_match_problems(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["match 必须是对象"]
    problems = []
    unknown = sorted(set(value) - {"sources", "pattern", "min_count"})
    if unknown:
        problems.append(f"match 里有未知键 {', '.join(unknown)}")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        problems.append("match.sources 必须是非空列表")
    else:
        unknown_roles = [str(role) for role in sources if str(role) not in LOG_ROLES]
        if unknown_roles:
            problems.append(f"未知的日志来源 {', '.join(unknown_roles)}（可用 {', '.join(LOG_ROLES)}）")
    pattern = value.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        problems.append("match.pattern 必须是非空字符串")
    else:
        try:
            re.compile(pattern)
        except re.error as exc:
            problems.append(f"match.pattern 不是合法正则（{exc}）")
    minimum = value.get("min_count")
    if minimum is not None and (not isinstance(minimum, int) or minimum < 1):
        problems.append("match.min_count 必须是正整数")
    return problems


def _diagnosis_go_to_problems(value: Any, package_ids: set[str] | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return ["go_to 必须是对象"]
    problems = []
    page = str(value.get("page") or "")
    if page not in GO_TO_PAGES:
        problems.append(f"go_to.page 只能是 {' / '.join(GO_TO_PAGES)}")
    package = str(value.get("package") or "")
    if package and package_ids is not None and package not in package_ids:
        problems.append(f"go_to.package 不是注册表里的包：{package}")
    return problems


def _diagnosis_entry_problems(entry: Any, package_ids: set[str] | None) -> list[str]:
    if not isinstance(entry, dict):
        return ["必须是对象"]
    unknown = sorted(set(entry) - DIAGNOSIS_ENTRY_KEYS)
    if unknown:
        return [f"未知键 {', '.join(unknown)}"]
    missing = sorted(key for key in ("id", "bucket", "level", "title") if not entry.get(key))
    if missing:
        return [f"缺少 {', '.join(missing)}"]

    problems: list[str] = []
    rule_id = str(entry["id"])
    if rule_id != entry["id"] or any(char.isspace() for char in rule_id):
        problems.append("id 必须是无空白的字符串")
    if entry["bucket"] not in BUCKETS:
        problems.append(f"bucket 只能是 {' / '.join(BUCKETS)}")
    level = entry.get("level")
    if not isinstance(level, int) or isinstance(level, bool) or not MIN_LEVEL <= level <= MAX_LEVEL:
        problems.append(f"level 必须是 {MIN_LEVEL}..{MAX_LEVEL} 的整数")

    has_check = bool(str(entry.get("check") or "").strip())
    has_match = entry.get("match") is not None
    if has_check == has_match:
        problems.append("check 与 match 必须恰好给一个")
    if has_check and str(entry["check"]) not in CHECKS:
        problems.append(f"未知的 check {entry['check']}（代码里有 {', '.join(CHECKS)}）")
    if has_match:
        problems.extend(_diagnosis_match_problems(entry["match"]))

    for name in DIAGNOSIS_TEXT_KEYS:
        if name == "tutorial":
            problems.extend(_diagnosis_tutorial_problems(entry.get(name)))
        else:
            problems.extend(_diagnosis_text_problems(entry.get(name), name))
    problems.extend(_diagnosis_go_to_problems(entry.get("go_to"), package_ids))
    return problems


def load_diagnosis_pack(
        path: Path,
        package_ids: set[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """读诊断规则包：整包坏了就是空包，单条坏了只跳过那一条并留告警。

    比客户端那份规范化严一档：文案要求中英齐全、`go_to.package` 必须对上注册表里的包 id。
    规则包走索引在线生效，这里是唯一的闸门 —— 客户端拿到手的必须是已经站得住的规则。
    """
    warnings: list[str] = []
    empty: dict[str, Any] = {"schema_version": 1, "pack_version": 0, "entries": []}
    if not path.is_file():
        return empty, warnings
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return empty, [f"{path.name}: 无法读取（{exc}）"]

    if not isinstance(raw, dict):
        return empty, [f"{path.name}: 顶层必须是对象"]
    unknown = sorted(set(raw) - {"schema_version", "pack_version", "entries"})
    if unknown:
        warnings.append(f"{path.name}: 忽略了未知键 {', '.join(unknown)}")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        warnings.append(f"{path.name}: entries 必须是非空列表")
        return empty, warnings

    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, entry in enumerate(entries, start=1):
        problems = _diagnosis_entry_problems(entry, package_ids)
        rule_id = str(entry.get("id") or "") if isinstance(entry, dict) else ""
        if not problems and rule_id in seen:
            problems = ["id 与前面某一条重复"]
        if problems:
            warnings.extend(f"{path.name} 第 {position} 条：{problem}" for problem in problems)
            continue
        seen.add(rule_id)
        kept.append(entry)

    if not kept:
        return empty, warnings
    pack = diagnosis_pack({**raw, "entries": kept})
    if len(pack["entries"]) != len(kept):
        # 显式校验会比规范化宽松一天两天，那等于放过了一条规则；宁可在这里炸出来。
        warnings.append(
            f"{path.name}: {len(kept) - len(pack['entries'])} 条规则通过了校验却没通过规范化"
        )
    return pack, warnings


def legacy_index(index: dict[str, Any]) -> dict[str, Any]:
    """单文件索引：`data/` 下那三份合并回一份，条目声明 v2。

    只读单文件的老客户端从站点根的 `index.json` 取这份。条目内容与 `data/` 同源，
    只把 `schema_version` 降到 2 —— 完整写出的条目在 v2 规则下同样合法。
    """
    return {
        "schema_version": index["schema_version"],
        "generated_at": index.get("generated_at", ""),
        "game": index.get("game") or {},
        "providers": index.get("providers") or {},
        "providers_warnings": index.get("providers_warnings") or [],
        "diagnosis": index.get("diagnosis") or {},
        "diagnosis_warnings": index.get("diagnosis_warnings") or [],
        "packages": [
            {**package, "schema_version": LEGACY_ENTRY_SCHEMA_VERSION}
            for package in index.get("packages") or []
        ],
    }


def write_index_files(index: dict[str, Any], output_dir: Path) -> None:
    """把索引写进输出目录：`data/` 下三份，站点根下一份兼容用的单文件。"""
    directory = output_dir / INDEX_DATA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        PACKAGES_FILE_NAME: {
            "schema_version": index["schema_version"],
            "generated_at": index.get("generated_at", ""),
            "packages": index.get("packages") or [],
        },
        ENVIRONMENT_FILE_NAME: {
            "schema_version": index["schema_version"],
            "game": index.get("game") or {},
            "providers": index.get("providers") or {},
            "providers_warnings": index.get("providers_warnings") or [],
        },
        DIAGNOSIS_FILE_NAME: dict(index.get("diagnosis") or {}),
    }
    for name, payload in files.items():
        (directory / name).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    (output_dir / LEGACY_INDEX_FILE_NAME).write_text(
        json.dumps(legacy_index(index), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def generate_index(
    mods_dir: Path,
    output_dir: Path,
    *,
    release_loader: Callable[[dict[str, Any], list[dict[str, Any]], dict[str, int]], list[dict[str, Any]]] | None = None,
    baseline_releases: dict[str, list[dict[str, Any]]] | None = None,
    fallback_index_url: str = FALLBACK_INDEX_URL,
    providers_file: Path | None = None,
    diagnosis_file: Path | None = None,
    override_file: Path | None = None,
    refresh: bool = False,
) -> dict:
    packages = scan_mods(mods_dir)
    by_id = {package["id"]: package for package in packages}
    capabilities = compat_capabilities(by_id)
    overrides, override_warnings = load_compat_overrides(
        override_file if override_file is not None else OVERRIDE_FILE,
        set(by_id),
        capabilities,
    )
    for warning in override_warnings:
        print(f"warning: {warning}", file=sys.stderr)
    baseline = {} if refresh else dict(baseline_releases or {})
    if release_loader:
        fallback_releases: dict[str, list[dict[str, Any]]] | None = None
        fallback_error: Exception | None = None

        def restore(package_id: str) -> list[dict[str, Any]] | None:
            """Releases for a package whose fetch failed: the baseline first, then the URL."""
            nonlocal fallback_releases, fallback_error
            if baseline.get(package_id) is not None:
                return baseline[package_id]
            if fallback_releases is None and fallback_error is None:
                try:
                    fallback_releases = load_index_releases(fallback_index_url)
                except Exception as exc:
                    fallback_error = exc
            if fallback_releases is None:
                return None
            return fallback_releases.get(package_id)

        for package in packages:
            if release_source_type(package) == "external":
                # 外部来源没有可查询的 API：版本与资产由条目自己带着，构建时原样写进索引。
                continue
            try:
                package["releases"] = release_loader(
                    package, baseline.get(package["id"], []), capabilities
                )
                continue
            except Exception as exc:
                release_error = exc

            releases = restore(package["id"])
            if releases is not None:
                package["releases"] = migrate_release_dependencies(releases)
                print(
                    f"warning: {package['id']}: release fetch failed ({release_error}); "
                    "restored releases from the previous index",
                    file=sys.stderr,
                )
            else:
                package["releases"] = []
                fallback_reason = (
                    str(fallback_error)
                    if fallback_error is not None
                    else f"package is missing from {fallback_index_url}"
                )
                print(
                    f"warning: {package['id']}: release fetch failed ({release_error}); "
                    f"no release data available ({fallback_reason})",
                    file=sys.stderr,
                )
    for package in packages:
        # 每个包都走一遍：增量基线里的条目同样要补上覆盖表那份声明，外部来源的条目也一样。
        entries = overrides.get(package["id"])
        releases = package.get("releases")
        if not entries or not isinstance(releases, list) or not releases:
            continue
        package["releases"] = apply_release_compatibility(releases, capabilities, entries)

    if release_loader:
        for package in packages:
            releases = package.get("releases") or []
            warnings = _package_compatibility_warnings(releases)
            if releases:
                warnings.extend(
                    f"{OVERRIDE_FILE_NAME}: 没有对上任何 release 的区间：{version}"
                    for version in unmatched_compat_overrides(
                        overrides.get(package["id"]) or [], releases
                    )
                )
            if warnings:
                package["compatibility_warnings"] = warnings

    packages = release_order(packages)

    providers, providers_warnings = load_providers_table(
        providers_file if providers_file is not None else PROVIDERS_FILE,
        {package_id for package_id, package in by_id.items() if is_loader_kind(package.get("kind"))},
    )
    for warning in providers_warnings:
        print(f"warning: {warning}", file=sys.stderr)

    diagnosis, diagnosis_warnings = load_diagnosis_pack(
        diagnosis_file if diagnosis_file is not None else DIAGNOSIS_FILE,
        set(by_id),
    )
    for warning in diagnosis_warnings:
        print(f"warning: {warning}", file=sys.stderr)

    index = {
        "schema_version": 1,
        "game": {"id": GAME_CAPABILITY_ID, "name": GAME_NAME},
        "providers": providers,
        "providers_warnings": providers_warnings,
        "diagnosis": diagnosis,
        "diagnosis_warnings": diagnosis_warnings,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "packages": packages,
    }
    write_index_files(index, output_dir)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Sprocket Pages registry index")
    parser.add_argument("--mods-dir", default="mods")
    parser.add_argument(
        "--output-dir",
        default="site",
        help="directory that receives packages.json, environment.json, and diagnosis.json",
    )
    parser.add_argument(
        "--fetch-releases",
        action="store_true",
        help="embed normalized GitHub Release data, reading only the releases that are new",
    )
    parser.add_argument(
        "--previous",
        default="",
        help=(
            "packages file or URL holding the releases already known "
            "(default: <output-dir>/packages.json when it exists, else --fallback-index-url)"
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the previous index and re-read every package's releases",
    )
    parser.add_argument(
        "--providers",
        default="",
        help=f"loader-package/game compatibility table (default: {PROVIDERS_FILE_NAME} in the repository root)",
    )
    parser.add_argument(
        "--diagnosis",
        default="",
        help=f"diagnosis rule pack (default: {DIAGNOSIS_FILE_NAME} in the repository root)",
    )
    parser.add_argument(
        "--override",
        default="",
        help=(
            "compatibility declarations that cover releases whose body has no sp-compat block "
            f"(default: {OVERRIDE_FILE_NAME} in the repository root)"
        ),
    )
    parser.add_argument(
        "--fallback-index-url",
        default=FALLBACK_INDEX_URL,
        help="packages URL used when no previous packages file is available",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    previous_packages = output_dir / INDEX_DATA_DIR / PACKAGES_FILE_NAME
    baseline: dict[str, list[dict[str, Any]]] = {}
    if args.fetch_releases and not args.refresh:
        source: str | Path = args.previous or (
            previous_packages if previous_packages.is_file() else args.fallback_index_url
        )
        try:
            baseline = load_index_releases(source)
        except (OSError, ValueError) as exc:
            print(f"warning: cannot read the previous index {source}: {exc}", file=sys.stderr)

    try:
        index = generate_index(
            Path(args.mods_dir),
            output_dir,
            release_loader=fetch_package_releases if args.fetch_releases else None,
            baseline_releases=baseline,
            fallback_index_url=args.fallback_index_url,
            providers_file=Path(args.providers) if args.providers else None,
            diagnosis_file=Path(args.diagnosis) if args.diagnosis else None,
            override_file=Path(args.override) if args.override else None,
            refresh=args.refresh,
        )
    except RegistryError as exc:
        print(f"registry error: {exc}")
        return 1

    reused = sum(
        1 for package in index["packages"] if baseline.get(package["id"]) == package.get("releases")
    )
    summary = f"generated {args.output_dir} with {len(index['packages'])} packages"
    if args.fetch_releases and not args.refresh:
        summary += f" ({reused} reused from the previous index, {len(index['packages']) - reused} refreshed)"
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
