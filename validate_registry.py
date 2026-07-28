#!/usr/bin/env python3
"""Validate registry metadata and its public GitHub source/release boundary."""

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

from sprocket_mod_manager.semver import Version


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


def matching_release_assets(meta: dict[str, Any], releases: list[dict[str, Any]]) -> list[str]:
    version_pattern = re.compile(meta["release"]["version_pattern"])
    include_prerelease = meta["release"]["include_prerelease"]
    includes = [pattern.casefold() for pattern in meta["release"]["assets"]["include"]]
    excludes = [pattern.casefold() for pattern in meta["release"]["assets"]["exclude"]]
    matches: list[str] = []
    for release in releases:
        if release.get("draft") or (release.get("prerelease") and not include_prerelease):
            continue
        tag = str(release.get("tag_name", ""))
        version_match = version_pattern.fullmatch(tag)
        if not version_match:
            continue
        try:
            version = Version.parse(version_match.group(1))
        except (IndexError, ValueError):
            continue
        if version.prerelease and not include_prerelease:
            continue
        for asset in release.get("assets") or []:
            name = str(asset.get("name", "")).casefold()
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in includes) and not any(
                fnmatch.fnmatchcase(name, pattern) for pattern in excludes
            ):
                matches.append(f"{tag}/{asset.get('name')}")
    return matches


def validate_online(meta: dict[str, Any], api: GitHubApi) -> list[str]:
    errors: list[str] = []
    repository = meta["repository"]
    encoded = "/".join(quote(part, safe="") for part in repository.split("/", 1))
    try:
        repo = api.get(f"/repos/{encoded}")
    except RuntimeError as exc:
        return [str(exc)]
    if repo.get("private"):
        errors.append("repository must be public")
    if repo.get("archived"):
        errors.append("repository must not be archived")
    license_id = (repo.get("license") or {}).get("spdx_id")
    if not license_id or license_id in {"NOASSERTION", "OTHER"}:
        errors.append("repository must have a recognized SPDX open-source license")

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
    if tree_available and not any(Path(path).name.casefold() in LICENSE_NAMES for path in paths):
        errors.append("repository must contain a LICENSE or COPYING file")
    if tree_available and not any(Path(path).suffix.casefold() in SOURCE_SUFFIXES for path in paths):
        errors.append("repository must contain source files, not only release binaries")

    releases_available = True
    try:
        releases = api.get(f"/repos/{encoded}/releases?per_page=100")
    except RuntimeError as exc:
        errors.append(str(exc))
        releases_available = False
        releases = []
    if releases_available and (not isinstance(releases, list) or not matching_release_assets(meta, releases)):
        errors.append("no compatible GitHub Release asset matches release.assets")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Sprocket mod registry")
    parser.add_argument("--mods-dir", default="mods")
    parser.add_argument("--offline", action="store_true", help="skip public GitHub checks")
    args = parser.parse_args()
    index_module = load_index_module()
    try:
        packages = index_module.scan_mods(Path(args.mods_dir))
    except Exception as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 1
    if args.offline:
        print(f"validated {len(packages)} registry entries (offline)")
        return 0

    api = GitHubApi()
    failures: list[str] = []
    for meta in packages:
        errors = validate_online(meta, api)
        if errors:
            failures.extend(f"{meta['id']}: {error}" for error in errors)
        else:
            print(f"validated {meta['id']} ({meta['repository']})")
    if failures:
        for failure in failures:
            print(f"registry error: {failure}", file=sys.stderr)
        return 1
    print(f"validated {len(packages)} registry entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
