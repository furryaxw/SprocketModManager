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

from sprocket_mod_manager.domain.semver import Version, validate_range


REQUIRED_FIELDS = {
    "schema_version",
    "id",
    "name",
    "authors",
    "repository",
    "license",
    "display_name",
    "release",
    "dependencies",
    "install",
    "category",
    "tags",
}
OPTIONAL_FIELDS = {"description", "recommendations", "featured"}
FORBIDDEN_VERSION_FIELDS = {"version", "latest_version", "download_url", "tag"}
ALLOWED_TARGET_ROOTS = {"Mods", "Plugins", "UserLibs", "UserData"}
ALLOWED_CATEGORIES = {"gameplay", "utility", "library", "visual", "audio", "translation", "other"}
XUNITY_TRANSLATION_MODE = "xunity-translation"
XUNITY_TRANSLATOR_PACKAGE_ID = "bbepis.xunity-auto-translator-melonmod-il2cpp"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
LANGUAGE_TAG_RE = re.compile(
    r"^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|[xX](?:-[A-Za-z0-9]{1,8})+)$"
)
GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
FALLBACK_INDEX_URL = "https://sprocketmods.furryaxw.top/index.json"
INSTALLABLE_SUFFIXES = {".dll", ".zip"}

# 环境用虚拟包表达：模组 release 对它们的依赖就是「兼容哪个游戏 / 哪个加载器」。
# 这两个 id 不在注册表里（没有目录、没有 release），解析时由客户端用本机环境注入。
# id 必须满足注册表的 id 规则（全小写、至少一段分隔符）：`MelonLoader` 这种大小写只出现在显示名里。
SPROCKET_GAME_PACKAGE = "environment.sprocket"
MELONLOADER_RUNTIME_PACKAGE = "environment.melonloader"
VIRTUAL_PACKAGES = (SPROCKET_GAME_PACKAGE, MELONLOADER_RUNTIME_PACKAGE)
# 轴 -> (版本段数, 虚拟包 id)
COMPAT_AXES: dict[str, tuple[int, str]] = {
    "sprocket": (4, SPROCKET_GAME_PACKAGE),
    "melonloader": (3, MELONLOADER_RUNTIME_PACKAGE),
}
COMPAT_BLOCK_RE = re.compile(r"<!--\s*sp-compat\s*(?P<body>.*?)-->", re.DOTALL | re.IGNORECASE)
# 环境表：加载器版本区间 -> 该加载器能跑的游戏版本区间。构建时规范化后一并写进索引，客户端解析索引即可拿到，不必额外请求。
ENVIRONMENT_FILE_NAME = "environment.json"
ENVIRONMENT_FILE = Path(__file__).resolve().parent / ENVIRONMENT_FILE_NAME


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


def canonical_compat_range(items: list[str], parts: int) -> str:
    """把作者写的一组区间项规范成一条 `>=a <=b`（多段用 `||` 连接）。"""
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


def parse_compat_block(body: object) -> tuple[dict[str, str], list[str]]:
    """发布说明里的 `sp-compat` 块 -> {轴: 规范化区间}，外加（非致命的）写法告警。

    没有块＝这个 release 没有声明（返回空，不报错）。块在但内容没法用 -> CompatibilityError，
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

    warnings: list[str] = []
    unknown = sorted(set(payload) - set(COMPAT_AXES))
    if unknown:
        warnings.append("已忽略未知的轴：" + ", ".join(unknown))

    declared: dict[str, str] = {}
    for axis, (parts, _package_id) in COMPAT_AXES.items():
        raw = payload.get(axis)
        if raw is None:
            continue
        items = [raw] if isinstance(raw, str) else raw
        if not isinstance(items, list) or not items or not all(isinstance(item, str) for item in items):
            raise CompatibilityError(f"{axis} 必须是版本区间字符串或它们的非空列表")
        declared[axis] = canonical_compat_range(list(items), parts)
    return declared, warnings


def apply_release_compatibility(releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把兼容声明落成对环境虚拟包的依赖，并处理继承。

    从旧到新走一遍：自己有可用声明就用自己那份；没有就沿用比它旧、最近一个有可用声明的
    release（`compatibility.from_tag` 始终指向**最初声明**的那个 tag，继承链不会越接越长）；
    写坏的声明不继承，只留告警。
    """
    ordered = sorted(releases, key=lambda entry: _release_version(entry) or Version.parse("0.0.0"))
    carried_dependencies: list[dict[str, str]] | None = None
    carried_origin = ""

    for entry in ordered:
        declared = entry.pop("compat_declared", None) or {}
        warnings = list(entry.pop("compat_warnings", None) or [])
        invalid = bool(entry.pop("compat_invalid", False))
        tag = str(entry.get("tag", ""))
        compatibility: dict[str, Any] = {}

        if declared:
            dependencies = [
                {"id": COMPAT_AXES[axis][1], "version": declared[axis]}
                for axis in COMPAT_AXES
                if axis in declared
            ]
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


def normalize_release_records(package: dict[str, Any], records: object) -> list[dict[str, Any]]:
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
            declared, compat_warnings = parse_compat_block(record.get("body"))
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
) -> list[dict[str, Any]]:
    """Read a package's releases, folding only the *new* ones into `known`.

    A small `/releases/latest` probe first: while the newest release is still the one we
    hold, the previous data is returned as it is and no release list is read at all.
    When it did change, the list is read and only the unseen releases are merged in.
    """
    previous = list(known or [])
    if previous:
        newest = newest_known_release(previous)
        probe = latest_release_record(package)
        if probe is not None and newest is not None and is_same_release(probe, newest):
            return previous

    encoded = _encoded_repository(package)
    records = _github_json(f"/repos/{encoded}/releases?per_page=100")
    try:
        normalized = normalize_release_records(package, records)
    except RegistryError as list_error:
        latest = _github_json(f"/repos/{encoded}/releases/latest", missing_ok=True)
        if not isinstance(latest, dict):
            raise list_error
        try:
            normalized = normalize_release_records(package, [latest])
        except RegistryError:
            raise list_error
    return apply_release_compatibility(merge_releases(previous, normalized))


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


def validate_meta(meta: dict, directory_name: str) -> None:
    missing = sorted(REQUIRED_FIELDS - set(meta))
    if missing:
        raise RegistryError(f"missing fields: {', '.join(missing)}")

    extra = sorted(set(meta) - REQUIRED_FIELDS - OPTIONAL_FIELDS - {"$schema"})
    if extra:
        raise RegistryError(f"unknown fields: {', '.join(extra)}")

    forbidden = sorted(FORBIDDEN_VERSION_FIELDS & set(meta))
    if forbidden:
        raise RegistryError(
            "Pages metadata must not contain release versions: " + ", ".join(forbidden)
        )

    if meta.get("schema_version") != 1:
        raise RegistryError("schema_version must be 1")
    package_id = meta.get("id", "")
    if not ID_RE.fullmatch(package_id):
        raise RegistryError(f"invalid package id: {package_id!r}")
    if package_id != directory_name:
        raise RegistryError(f"directory {directory_name!r} must match id {package_id!r}")
    if not REPOSITORY_RE.fullmatch(meta.get("repository", "")):
        raise RegistryError(f"invalid GitHub repository: {meta.get('repository')!r}")
    if not meta.get("license"):
        raise RegistryError("license is required")
    if not isinstance(meta.get("authors"), list) or not meta["authors"]:
        raise RegistryError("authors must be a non-empty list")
    if not all(isinstance(author, str) and author.strip() for author in meta["authors"]):
        raise RegistryError("authors must contain non-empty strings")
    validate_localized(meta.get("display_name"), "display_name")
    if "description" in meta:
        validate_localized(meta["description"], "description")
    if meta.get("category") not in ALLOWED_CATEGORIES:
        raise RegistryError(f"invalid category: {meta.get('category')!r}")
    tags = meta.get("tags")
    if not isinstance(tags, list) or len(tags) != len(set(tags)):
        raise RegistryError("tags must be a list without duplicates")
    if not all(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", tag or "") for tag in tags):
        raise RegistryError("tags must use lowercase letters, digits, and hyphens")

    release = meta.get("release")
    if not isinstance(release, dict):
        raise RegistryError("release must be an object")
    if set(release) != {"include_prerelease", "version_pattern", "assets"}:
        raise RegistryError("release requires exactly include_prerelease, version_pattern, and assets")
    if not isinstance(release.get("include_prerelease"), bool):
        raise RegistryError("release.include_prerelease must be a boolean")
    pattern = release.get("version_pattern", "")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise RegistryError(f"invalid version_pattern: {exc}") from exc
    if compiled.groups < 1:
        raise RegistryError("version_pattern must contain a capture group for SemVer")
    assets = release.get("assets", {})
    if not isinstance(assets.get("include"), list) or not assets["include"]:
        raise RegistryError("release.assets.include must be a non-empty list")
    if not isinstance(assets.get("exclude", []), list):
        raise RegistryError("release.assets.exclude must be a list")
    if set(assets) != {"include", "exclude"}:
        raise RegistryError("release.assets requires exactly include and exclude")
    if not all(isinstance(item, str) and item for item in assets["include"] + assets["exclude"]):
        raise RegistryError("release asset patterns must be non-empty strings")

    dependencies = meta.get("dependencies")
    if not isinstance(dependencies, list):
        raise RegistryError("dependencies must be a list")
    seen_rules: set[tuple[str, str]] = set()
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            raise RegistryError("dependency entries must be objects")
        required = {"id", "version", "when"}
        if set(dependency) != required:
            raise RegistryError(f"dependency must contain exactly {sorted(required)}")
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
    if set(install) not in (
        {"scan_dlls", "exclude", "overrides"},
        {"scan_dlls", "exclude", "overrides", "mode"},
    ):
        raise RegistryError("install requires scan_dlls, exclude, overrides, and optional mode")
    if not isinstance(install.get("scan_dlls"), bool):
        raise RegistryError("install.scan_dlls must be a boolean")
    if not isinstance(install.get("exclude", []), list):
        raise RegistryError("install.exclude must be a list")
    if not isinstance(install.get("overrides", []), list):
        raise RegistryError("install.overrides must be a list")
    mode = install.get("mode", "standard")
    if mode not in {"standard", XUNITY_TRANSLATION_MODE}:
        raise RegistryError(f"invalid install mode: {mode!r}")
    for override in install.get("overrides", []):
        if not isinstance(override, dict):
            raise RegistryError("install overrides must be objects")
        if set(override) != {"match", "target"}:
            raise RegistryError("install override requires exactly match and target")
        if not isinstance(override["match"], str) or not override["match"]:
            raise RegistryError("install override match must be a non-empty string")
        validate_target(override["target"])

    is_translation = meta.get("category") == "translation"
    if is_translation != (mode == XUNITY_TRANSLATION_MODE):
        raise RegistryError(
            "translation category packages must use install.mode xunity-translation"
        )
    if is_translation:
        if install["scan_dlls"] or install["overrides"]:
            raise RegistryError("XUnity translation packages cannot scan DLLs or use overrides")
        dependency_ids = {dependency["id"] for dependency in dependencies}
        if XUNITY_TRANSLATOR_PACKAGE_ID not in dependency_ids:
            raise RegistryError(
                f"translation packages must depend on {XUNITY_TRANSLATOR_PACKAGE_ID}"
            )


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
            cycle = visit(dependency["id"])
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


def scan_mods(mods_dir: Path) -> list[dict]:
    if not mods_dir.is_dir():
        raise RegistryError(f"mods directory does not exist: {mods_dir}")

    packages: dict[str, dict] = {}
    for mod_dir in sorted(path for path in mods_dir.iterdir() if path.is_dir()):
        meta_path = mod_dir / "sprocket-mod.json"
        if not meta_path.is_file():
            raise RegistryError(f"{mod_dir.name}: missing sprocket-mod.json")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"{mod_dir.name}: cannot read metadata: {exc}") from exc

        validate_meta(meta, mod_dir.name)
        package = {key: value for key, value in meta.items() if key != "$schema"}
        package["meta_url"] = f"mods/{mod_dir.name}/sprocket-mod.json"
        packages[package["id"]] = package

    for package in packages.values():
        for dependency in package.get("dependencies", []):
            if dependency["id"] in VIRTUAL_PACKAGES:
                # 环境虚拟包：模组声明「兼容哪个游戏 / 哪个加载器」时用它，注册表里没有对应目录。
                continue
            if dependency["id"] not in packages:
                raise RegistryError(
                    f"{package['id']}: dependency is not registered: {dependency['id']}"
                )
        for recommendation in package.get("recommendations", []):
            if recommendation not in packages:
                raise RegistryError(
                    f"{package['id']}: recommendation is not registered: {recommendation}"
                )

    cycle = find_dependency_cycle(packages)
    if cycle:
        raise RegistryError("dependency cycle: " + " -> ".join(cycle))

    return [packages[key] for key in sorted(packages)]


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


def load_environment_table(path: Path) -> tuple[dict[str, Any], list[str]]:
    """读加载器-游戏兼容表：每条记「某段加载器版本能跑哪段游戏版本」。

    返回值是 (表, 告警)。文件不存在就是没有这张表（客户端退回内置兜底）；某一条写坏只跳过
    那一条并留告警，不影响其他条目，也不影响索引生成。
    """
    warnings: list[str] = []
    empty: dict[str, Any] = {"schema_version": 1, "entries": []}
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
        missing = sorted({"melonloader", "sprocket"} - set(entry))
        if missing:
            warnings.append(f"{path.name} 第 {position} 条：缺少 {', '.join(missing)}")
            continue
        try:
            normalized.append(
                {
                    "melonloader": canonical_compat_range(
                        [str(entry["melonloader"])], COMPAT_AXES["melonloader"][0]
                    ),
                    "sprocket": canonical_compat_range(
                        [str(entry["sprocket"])], COMPAT_AXES["sprocket"][0]
                    ),
                }
            )
        except CompatibilityError as exc:
            warnings.append(f"{path.name} 第 {position} 条：{exc}")

    if not normalized:
        return empty, warnings
    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, int):
        schema_version = 1
    return {"schema_version": schema_version, "entries": normalized}, warnings


def generate_index(
    mods_dir: Path,
    output: Path,
    *,
    release_loader: Callable[[dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    baseline_releases: dict[str, list[dict[str, Any]]] | None = None,
    fallback_index_url: str = FALLBACK_INDEX_URL,
    environment_file: Path | None = None,
    refresh: bool = False,
) -> dict:
    packages = scan_mods(mods_dir)
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
            try:
                package["releases"] = release_loader(package, baseline.get(package["id"], []))
                continue
            except Exception as exc:
                release_error = exc

            releases = restore(package["id"])
            if releases is not None:
                package["releases"] = releases
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
    if release_loader:
        for package in packages:
            warnings = _package_compatibility_warnings(package.get("releases") or [])
            if warnings:
                package["compatibility_warnings"] = warnings

    environment, environment_warnings = load_environment_table(
        environment_file if environment_file is not None else ENVIRONMENT_FILE
    )
    for warning in environment_warnings:
        print(f"warning: {warning}", file=sys.stderr)

    index = {
        "schema_version": 1,
        "game": "sprocket",
        "virtual_packages": list(VIRTUAL_PACKAGES),
        "environment": environment,
        "environment_warnings": environment_warnings,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "packages": packages,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Sprocket Pages registry index")
    parser.add_argument("--mods-dir", default="mods")
    parser.add_argument("--output", default="index.json")
    parser.add_argument(
        "--fetch-releases",
        action="store_true",
        help="embed normalized GitHub Release data, reading only the releases that are new",
    )
    parser.add_argument(
        "--previous",
        default="",
        help=(
            "index file or URL holding the releases already known "
            "(default: the output file when it exists, else --fallback-index-url)"
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the previous index and re-read every package's releases",
    )
    parser.add_argument(
        "--environment",
        default="",
        help=f"loader/game compatibility table (default: {ENVIRONMENT_FILE_NAME} in the repository root)",
    )
    parser.add_argument(
        "--fallback-index-url",
        default=FALLBACK_INDEX_URL,
        help="index URL used when no previous index is available",
    )
    args = parser.parse_args()

    output = Path(args.output)
    baseline: dict[str, list[dict[str, Any]]] = {}
    if args.fetch_releases and not args.refresh:
        source: str | Path = args.previous or (output if output.is_file() else args.fallback_index_url)
        try:
            baseline = load_index_releases(source)
        except (OSError, ValueError) as exc:
            print(f"warning: cannot read the previous index {source}: {exc}", file=sys.stderr)

    try:
        index = generate_index(
            Path(args.mods_dir),
            output,
            release_loader=fetch_package_releases if args.fetch_releases else None,
            baseline_releases=baseline,
            fallback_index_url=args.fallback_index_url,
            environment_file=Path(args.environment) if args.environment else None,
            refresh=args.refresh,
        )
    except RegistryError as exc:
        print(f"registry error: {exc}")
        return 1

    reused = sum(
        1 for package in index["packages"] if baseline.get(package["id"]) == package.get("releases")
    )
    summary = f"generated {args.output} with {len(index['packages'])} packages"
    if args.fetch_releases and not args.refresh:
        summary += f" ({reused} reused from the previous index, {len(index['packages']) - reused} refreshed)"
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
