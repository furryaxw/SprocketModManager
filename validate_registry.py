#!/usr/bin/env python3
"""Check registry metadata and its public GitHub source/release boundary.

每个问题都只报出来：一个条目写坏不该拦住整站发布。只有读不动注册表本身时退出码才是 1。
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from registry_core.errors import ScanError
from registry_core.pack_manifest import parse_pack_manifest
from registry_core.semver import Version


SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".fs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".py",
    ".rs",
    ".ts",
    ".vb",
}
LICENSE_NAMES = {"license", "license.md", "license.txt", "copying", "copying.md", "copying.txt"}
# 条目用这个占位值声明许可证还没定：SPDX 与 LICENSE 文件两项检查豁免。
PLACEHOLDER_LICENSE = "TODO-SPDX"
# 整合包：成员与落点规则写在 release 的清单资产里，条目可以只留身份与抓取规则。
PACK_KIND = "pack"
MANIFEST_SUFFIX = ".json"
# 清单只是成员与规则的列表；下载它时给一个比它大得多的上限，挡住拿错文件。
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
# 整合包逐版读清单：版本太多的包只读最新的这些，剩下的交给索引构建去读。
MAX_PACK_RELEASES = 30


def load_index_module():
    path = Path(__file__).with_name("gen-index.py")
    spec = importlib.util.spec_from_file_location("sprocket_gen_index", path)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load gen-index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GitHubApi:
    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN")

    def get(self, path: str) -> Any:
        url = "https://api.github.com" + path
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "sprocket-mod-registry-validator/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        last_error: Exception | None = None
        for attempt in range(3):
            request = Request(url, headers=headers)
            try:
                with urlopen(request, timeout=15) as response:
                    return json.load(response)
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise RuntimeError(f"GitHub API returned HTTP {exc.code} for {path}") from exc
            except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
        raise RuntimeError(f"GitHub API request failed for {path}: {last_error}") from last_error


def _release_version(meta: dict[str, Any], release: dict[str, Any]) -> str | None:
    """这条 release 落在条目的 `release` 规则里时给出它的版本；不在里面给 None。"""
    rules = meta.get("release")
    if not isinstance(rules, dict) or not isinstance(rules.get("version_pattern"), str):
        return None
    if release.get("draft") or (release.get("prerelease") and not rules.get("include_prerelease")):
        return None
    tag = str(release.get("tag_name", ""))
    match = re.compile(rules["version_pattern"]).fullmatch(tag)
    if not match:
        return None
    try:
        return str(Version.parse(match.group(1)))
    except (IndexError, ValueError):
        return None


def _matching_asset_names(meta: dict[str, Any], release: dict[str, Any]) -> list[str]:
    """这条 release 里匹配 `release.assets` 的资产名。"""
    rules = meta.get("release")
    if not isinstance(rules, dict):
        return []
    assets = rules.get("assets") if isinstance(rules.get("assets"), dict) else {}
    includes = [pattern.casefold() for pattern in assets.get("include") or []]
    excludes = [pattern.casefold() for pattern in assets.get("exclude") or []]
    names: list[str] = []
    for asset in release.get("assets") or []:
        name = str(asset.get("name", "")).casefold()
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in includes) and not any(
            fnmatch.fnmatchcase(name, pattern) for pattern in excludes
        ):
            names.append(str(asset.get("name", "")))
    return names


def matching_release_assets(meta: dict[str, Any], releases: list[dict[str, Any]]) -> list[str]:
    """Released assets that match the entry's `release` rules; no rules mean nothing to match."""
    matches: list[str] = []
    for release in releases:
        if _release_version(meta, release) is None:
            continue
        tag = str(release.get("tag_name", ""))
        matches.extend(f"{tag}/{name}" for name in _matching_asset_names(meta, release))
    return matches


def unmatched_release_tags(meta: dict[str, Any], releases: list[dict[str, Any]]) -> list[str]:
    """匹配 `version_pattern` 却一个资产都不匹配的 tag。

    收窄 `release.assets.include` 或改正则会让这些 release 从索引里消失（`gen-index.py` 对它们
    `continue`），客户端的回退选项跟着没了。
    """
    return [
        str(release.get("tag_name", ""))
        for release in releases
        if _release_version(meta, release) is not None and not _matching_asset_names(meta, release)
    ]


def _download_bytes(url: str, *, limit: int) -> bytes:
    """取回一个 release 资产（整合包的清单）；超过上限就报错。"""
    request = Request(url, headers={"User-Agent": "sprocket-mod-registry-validator/1"})
    with urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise OSError(f"asset is larger than {limit} bytes: {url}")
    return data


def _manifest_assets(release: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        asset
        for asset in release.get("assets") or []
        if Path(str(asset.get("name", ""))).suffix.casefold() == MANIFEST_SUFFIX
    ]


def validate_releases(
        meta: dict[str, Any],
        api: "GitHubApi",
        *,
        index_module,
        packages: dict[str, dict[str, Any]],
        game_supply: dict[str, str],
        published_tags: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """按版本核对 release：每个版本都要有匹配的资产，整合包还要读得动它的清单。

    某个 release 掉了资产是**错误**当且仅当已发布索引里还有它 —— 那正是"老版本被静默削掉"。
    其余情况（从来没进过索引的 tag）只给警告。整合包的清单是成员与落点规则的来源，所以逐版
    读回来验：成员要已注册、区间要读得动、规则的类型要有人供给。
    """
    rules = meta.get("release")
    if not isinstance(rules, dict) or not isinstance(rules.get("version_pattern"), str):
        return [], []
    repository = str(meta.get("repository") or "")
    if not repository:
        return [], []
    encoded = "/".join(quote(part, safe="") for part in repository.split("/", 1))
    try:
        releases = api.get(f"/repos/{encoded}/releases?per_page=100")
    except RuntimeError as exc:
        return [str(exc)], []
    if not isinstance(releases, list):
        return ["GitHub Releases response is not a list"], []

    errors: list[str] = []
    warnings: list[str] = []
    for tag in unmatched_release_tags(meta, releases):
        message = (
            f"release {tag} matches release.version_pattern but no asset matches release.assets; "
            "it would disappear from the index"
        )
        if tag in published_tags:
            errors.append(f"{message} (the published index still lists it)")
        else:
            warnings.append(message)

    if str(meta.get("kind", "modfile")) != PACK_KIND:
        return errors, warnings

    checked = 0
    for release in releases:
        if _release_version(meta, release) is None:
            continue
        if checked >= MAX_PACK_RELEASES:
            warnings.append(
                f"only the newest {MAX_PACK_RELEASES} releases were checked for a manifest"
            )
            break
        checked += 1
        tag = str(release.get("tag_name", ""))
        assets = _manifest_assets(release)
        if len(assets) > 1:
            errors.append(
                f"release {tag} carries {len(assets)} manifest assets "
                f"({', '.join(str(asset.get('name', '')) for asset in assets)}); "
                "a release carries one"
            )
            continue
        if not assets:
            errors.append(f"release {tag} carries no manifest asset ({MANIFEST_SUFFIX})")
            continue
        asset = assets[0]
        try:
            raw = _download_bytes(
                str(asset.get("browser_download_url", "")), limit=MAX_MANIFEST_BYTES
            )
        except (HTTPError, URLError, OSError) as exc:
            errors.append(f"release {tag}: cannot download the manifest ({exc})")
            continue
        try:
            manifest = parse_pack_manifest(raw, source=f"{meta['id']} {tag}")
        except ScanError as exc:
            errors.append(str(exc))
            continue
        errors.extend(_manifest_member_errors(meta, tag, manifest, packages, index_module))
        if manifest.install:
            try:
                errors.extend(
                    f"release {tag}: {issue}"
                    for issue in _validate_manifest_rules(
                        index_module, meta, manifest.install, packages, game_supply
                    )
                )
            except index_module.RegistryError as exc:
                errors.append(f"release {tag}: {exc}")
    return errors, warnings


def _manifest_member_errors(meta, tag, manifest, packages, index_module) -> list[str]:
    errors: list[str] = []
    for member in manifest.members:
        if member["id"] not in packages:
            errors.append(f"release {tag}: manifest member is not registered: {member['id']}")
        try:
            index_module.validate_range(member["version"])
        except ValueError:
            errors.append(
                f"release {tag}: manifest member {member['id']} has an unusable version range: "
                f"{member['version']!r}"
            )
    return errors


def _validate_manifest_rules(index_module, meta, install, packages, game_supply) -> list[str]:
    """清单里的落点规则过一遍注册表自己的校验器：形状、replace 的配套、类型有没有供给者。"""
    if "files" in install:
        index_module.validate_file_rules(install["files"])
    if "payload" in install:
        index_module.validate_payload_rules(install["payload"])
    if "files" in install and "payload" in install:
        raise index_module.RegistryError("a pack must not mix install.files and install.payload")
    index_module.validate_replace_types(install, install.get("mode", "standard"))
    # 类型必须有供给者：拿一份临时条目跑注册表那套检查，供给表用的是整份注册表。
    return index_module.validate_install_types(
        {**packages, meta["id"]: {**meta, "install": install}}, game_supply
    )


def validate_online(meta: dict[str, Any], api: GitHubApi) -> list[str]:
    errors: list[str] = []
    repository = str(meta.get("repository") or "")
    if not repository:
        # 没有仓库地址就没有可核对的公开来源。
        return []
    encoded = "/".join(quote(part, safe="") for part in repository.split("/", 1))
    try:
        repo = api.get(f"/repos/{encoded}")
    except RuntimeError as exc:
        return [str(exc)]
    if repo.get("private"):
        errors.append("repository must be public")
    pending_license = str(meta.get("license", "")).strip().casefold() == PLACEHOLDER_LICENSE.casefold()
    license_id = (repo.get("license") or {}).get("spdx_id")
    recognized_license = bool(license_id) and license_id not in {"NOASSERTION", "OTHER"}

    branch = quote(str(repo.get("default_branch", "")), safe="")
    tree_available = True
    try:
        tree = api.get(f"/repos/{encoded}/git/trees/{branch}?recursive=1")
    except RuntimeError as exc:
        errors.append(str(exc))
        tree_available = False
        tree = {"tree": []}
    if tree_available and tree.get("truncated"):
        errors.append("repository tree is too large to verify source presence")
    paths = [str(item.get("path", "")) for item in tree.get("tree") or [] if item.get("type") == "blob"]
    license_file = any(Path(path).name.casefold() in LICENSE_NAMES for path in paths)
    if tree_available and not pending_license and not license_file:
        errors.append("repository must contain a LICENSE or COPYING file")
        # 文件是现成的，GitHub 认不出它（LGPL 文本后接了 GPL 全文这类）不算失败：以条目声明为准。
        if not recognized_license:
            errors.append("repository must have a recognized SPDX open-source license")
    if tree_available and not any(Path(path).suffix.casefold() in SOURCE_SUFFIXES for path in paths):
        errors.append("repository must contain source files, not only release binaries")

    releases_available = True
    try:
        releases = api.get(f"/repos/{encoded}/releases?per_page=100")
    except RuntimeError as exc:
        errors.append(str(exc))
        releases_available = False
        releases = []
    if releases_available and releases == []:
        try:
            latest = api.get(f"/repos/{encoded}/releases/latest")
        except RuntimeError:
            latest = None
        if isinstance(latest, dict):
            releases = [latest]
    release = meta.get("release")
    if releases_available and isinstance(release, dict) and release.get("version_pattern"):
        if not isinstance(releases, list) or not matching_release_assets(meta, releases):
            errors.append("no compatible GitHub Release asset matches release.assets")
    return errors


def validate_game(index_module) -> list[str]:
    """The game's own identity and supply table: a mod may install into the game's folders, so a
    missing or unusable one would leave those install rules without a supplier."""
    path = index_module.GAME_FILE
    if not path.is_file():
        return [f"{path.name} is missing"]
    game, warnings = index_module.load_game_file(path)
    errors = list(warnings)
    if not game.get("supply"):
        errors.append(f"{path.name} has no usable supply entry")
    return errors


def validate_providers(index_module, loader_ids) -> list[str]:
    """The loader-package/game compatibility table: a platform fact, so a bad entry is reported here
    instead of silently narrowing what the client can filter."""
    path = index_module.PROVIDERS_FILE
    if not path.is_file():
        return [f"{path.name} is missing"]
    table, warnings = index_module.load_providers_table(path, loader_ids)
    errors = list(warnings)
    if not table["entries"]:
        errors.append(f"{path.name} has no usable entry")
    return errors


def validate_overrides(index_module, packages) -> list[str]:
    """The compatibility override table: a broken range or a stale package id would leave some
    release without a declaration, so it is reported here. Having no entry is a normal state."""
    path = index_module.OVERRIDE_FILE
    if not path.is_file():
        return [f"{path.name} is missing"]
    _, warnings = index_module.load_compat_overrides(
        path,
        {package["id"] for package in packages},
        index_module.compat_capabilities({package["id"]: package for package in packages}),
    )
    return list(warnings)


def validate_diagnosis(index_module, package_ids) -> list[str]:
    """The diagnosis rule pack ships to clients through the index, so an entry the loader drops
    has to be reported here: a rule that never fires is worse than a rule that was never written."""
    path = index_module.DIAGNOSIS_FILE
    if not path.is_file():
        return [f"{path.name} is missing"]
    pack, warnings = index_module.load_diagnosis_pack(path, package_ids)
    errors = list(warnings)
    if not pack["entries"]:
        errors.append(f"{path.name} has no usable rule")
    return errors


def _published_release_tags(index_module) -> dict[str, frozenset[str]]:
    """已发布索引里每个包带着的 tag：拿它区分"老版本被削掉"和"从来没进过索引"。

    读不到就不比较历史（只留警告），别让一次网络失败把提交拦下来。
    """
    try:
        releases = index_module.load_index_releases(index_module.FALLBACK_INDEX_URL)
    except Exception as exc:
        print(
            f"warning: cannot read the published index ({exc}); release history is not compared",
            file=sys.stderr,
        )
        return {}
    return {
        package_id: frozenset(str(record.get("tag", "")) for record in records)
        for package_id, records in releases.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Sprocket mod registry")
    parser.add_argument("--mods-dir", default="packages")
    parser.add_argument("--offline", action="store_true", help="skip public GitHub checks")
    args = parser.parse_args()
    index_module = load_index_module()
    game, _game_warnings = index_module.load_game_file(index_module.GAME_FILE)
    try:
        packages = index_module.scan_mods(Path(args.mods_dir), game=game)
    except Exception as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 1
    problems: list[str] = [
        f"game: {error}" for error in validate_game(index_module)
    ]
    problems.extend(
        f"providers: {error}"
        for error in validate_providers(
            index_module,
            {
                package["id"]
                for package in packages
                if index_module.is_loader_kind(package.get("kind"))
            },
        )
    )
    problems.extend(
        f"diagnosis: {error}"
        for error in validate_diagnosis(index_module, {package["id"] for package in packages})
    )
    problems.extend(
        f"override: {error}" for error in validate_overrides(index_module, packages)
    )
    for problem in problems:
        print(f"warning: {problem}", file=sys.stderr)
    if args.offline:
        print(f"validated {len(packages)} registry entries (offline)")
        return 0

    api = GitHubApi()
    by_id = {package["id"]: package for package in packages}
    published = _published_release_tags(index_module)
    for meta in packages:
        errors = validate_online(meta, api)
        release_errors, release_warnings = validate_releases(
            meta,
            api,
            index_module=index_module,
            packages=by_id,
            game_supply=game.get("supply") or {},
            published_tags=published.get(meta["id"], frozenset()),
        )
        errors.extend(release_errors)
        for warning in release_warnings:
            print(f"warning: {meta['id']}: {warning}", file=sys.stderr)
        for error in errors:
            print(f"warning: {meta['id']}: {error}", file=sys.stderr)
        if not errors:
            print(f"validated {meta['id']} ({meta['repository']})")
    print(f"validated {len(packages)} registry entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
