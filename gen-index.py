#!/usr/bin/env python3
"""Validate registry metadata and build the static Pages index."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sprocket_mod_manager.semver import validate_range


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
OPTIONAL_FIELDS = {"description"}
FORBIDDEN_VERSION_FIELDS = {"version", "latest_version", "download_url", "tag"}
ALLOWED_TARGET_ROOTS = {"Mods", "Plugins", "UserLibs", "UserData"}
ALLOWED_CATEGORIES = {"gameplay", "utility", "library", "visual", "audio", "other"}
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
LANGUAGE_TAG_RE = re.compile(
    r"^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|[xX](?:-[A-Za-z0-9]{1,8})+)$"
)


class RegistryError(ValueError):
    pass


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

    install = meta.get("install")
    if not isinstance(install, dict):
        raise RegistryError("install must be an object")
    if set(install) != {"scan_dlls", "exclude", "overrides"}:
        raise RegistryError("install requires exactly scan_dlls, exclude, and overrides")
    if not isinstance(install.get("scan_dlls"), bool):
        raise RegistryError("install.scan_dlls must be a boolean")
    if not isinstance(install.get("exclude", []), list):
        raise RegistryError("install.exclude must be a list")
    if not isinstance(install.get("overrides", []), list):
        raise RegistryError("install.overrides must be a list")
    for override in install.get("overrides", []):
        if not isinstance(override, dict):
            raise RegistryError("install overrides must be objects")
        if set(override) != {"match", "target"}:
            raise RegistryError("install override requires exactly match and target")
        if not isinstance(override["match"], str) or not override["match"]:
            raise RegistryError("install override match must be a non-empty string")
        validate_target(override["target"])


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
            if dependency["id"] not in packages:
                raise RegistryError(
                    f"{package['id']}: dependency is not registered: {dependency['id']}"
                )

    cycle = find_dependency_cycle(packages)
    if cycle:
        raise RegistryError("dependency cycle: " + " -> ".join(cycle))

    return [packages[key] for key in sorted(packages)]


def generate_index(mods_dir: Path, output: Path) -> dict:
    packages = scan_mods(mods_dir)
    index = {
        "schema_version": 1,
        "game": "sprocket",
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
    args = parser.parse_args()
    try:
        index = generate_index(Path(args.mods_dir), Path(args.output))
    except RegistryError as exc:
        print(f"registry error: {exc}")
        return 1
    print(f"generated {args.output} with {len(index['packages'])} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
