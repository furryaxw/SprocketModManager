from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .semver import Version


ProgressCallback = Callable[[str], None]


def localized_value(values: dict[str, str], language: str = "en") -> str:
    if not values:
        return ""

    folded = {key.casefold(): value for key, value in values.items()}
    requested = language.replace("_", "-").casefold()
    if requested in folded:
        return folded[requested]

    def find_language(base: str) -> str:
        if base in folded:
            return folded[base]
        return next(
            (value for key, value in values.items() if key.casefold().split("-", 1)[0] == base),
            "",
        )

    requested_base = requested.split("-", 1)[0]
    return find_language(requested_base) or find_language("en") or next(iter(values.values()))


@dataclass(frozen=True)
class RegistryPackage:
    id: str
    name: str
    authors: tuple[str, ...]
    repository: str
    license: str
    display_name: dict[str, str]
    description: dict[str, str]
    release: dict[str, Any]
    dependencies: tuple[dict[str, str], ...]
    install: dict[str, Any]
    category: str
    tags: tuple[str, ...]
    meta_url: str = ""
    releases: tuple["ReleaseInfo", ...] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RegistryPackage":
        raw_releases = data.get("releases")
        if raw_releases is not None and not isinstance(raw_releases, list):
            raise TypeError("releases must be a list")
        releases = (
            None
            if raw_releases is None
            else tuple(ReleaseInfo.from_dict(item, repository=str(data["repository"])) for item in raw_releases)
        )
        release_rules = dict(data.get("release", {}))
        if releases is not None:
            try:
                version_pattern = re.compile(str(release_rules["version_pattern"]))
            except (KeyError, re.error) as exc:
                raise ValueError("invalid release version pattern") from exc
            for release in releases:
                match = version_pattern.fullmatch(release.tag)
                if not match or Version.parse(match.group(1)) != release.version:
                    raise ValueError(f"embedded release does not match version pattern: {release.tag}")
                if release.prerelease and not release_rules.get("include_prerelease"):
                    raise ValueError(f"embedded prerelease is not allowed: {release.tag}")
        return cls(
            id=data["id"],
            name=data["name"],
            authors=tuple(data.get("authors", ())),
            repository=data["repository"],
            license=data.get("license", ""),
            display_name=dict(data.get("display_name", {})),
            description=dict(data.get("description", {})),
            release=release_rules,
            dependencies=tuple(dict(item) for item in data.get("dependencies", ())),
            install=dict(data.get("install", {})),
            category=data.get("category", "other"),
            tags=tuple(data.get("tags", ())),
            meta_url=data.get("meta_url", ""),
            releases=releases,
        )

    def label(self, language: str = "en") -> str:
        return localized_value(self.display_name, language) or self.name

    def description_text(self, language: str = "en") -> str:
        return localized_value(self.description, language)


@dataclass(frozen=True)
class ReleaseAsset:
    id: int
    name: str
    size: int
    download_url: str
    digest: str | None = None
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, repository: str = "") -> "ReleaseAsset":
        if not isinstance(data, dict):
            raise TypeError("release asset must be an object")
        download_url = str(data["download_url"])
        if repository:
            parsed = urlparse(download_url)
            expected_prefix = f"/{repository}/releases/download/".casefold()
            if (
                parsed.scheme != "https"
                or (parsed.hostname or "").casefold() != "github.com"
                or not parsed.path.casefold().startswith(expected_prefix)
            ):
                raise ValueError(f"invalid embedded release asset URL: {download_url}")
        return cls(
            id=int(data.get("id", 0)),
            name=str(data["name"]),
            size=int(data.get("size", 0)),
            download_url=download_url,
            digest=str(data["digest"]) if data.get("digest") else None,
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass(frozen=True)
class ReleaseInfo:
    id: int
    tag: str
    version: Version
    prerelease: bool
    published_at: str
    assets: tuple[ReleaseAsset, ...]
    page_url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, repository: str = "") -> "ReleaseInfo":
        if not isinstance(data, dict):
            raise TypeError("release must be an object")
        page_url = str(data.get("page_url", ""))
        if repository:
            parsed = urlparse(page_url)
            expected_prefix = f"/{repository}/releases/tag/".casefold()
            if (
                parsed.scheme != "https"
                or (parsed.hostname or "").casefold() != "github.com"
                or not parsed.path.casefold().startswith(expected_prefix)
            ):
                raise ValueError(f"invalid embedded release page URL: {page_url}")
        return cls(
            id=int(data.get("id", 0)),
            tag=str(data["tag"]),
            version=Version.parse(str(data["version"])),
            prerelease=bool(data.get("prerelease")),
            published_at=str(data.get("published_at", "")),
            assets=tuple(
                ReleaseAsset.from_dict(item, repository=repository)
                for item in data.get("assets", ())
            ),
            page_url=page_url,
        )


@dataclass(frozen=True)
class ResolvedPackage:
    package: RegistryPackage
    release: ReleaseInfo
    dependency_ids: tuple[str, ...]


@dataclass(frozen=True)
class ResolutionPlan:
    root_id: str
    packages: tuple[ResolvedPackage, ...]

    def by_id(self) -> dict[str, ResolvedPackage]:
        return {item.package.id: item for item in self.packages}


@dataclass(frozen=True)
class PreparedFile:
    package_id: str
    source: Path
    source_name: str
    target: str
    sha256: str


@dataclass(frozen=True)
class PreparedAsset:
    asset: ReleaseAsset
    path: Path
    sha256: str
    publisher_verified: bool
    publisher_digest: str | None


@dataclass
class PreparedPackage:
    resolved: ResolvedPackage
    assets: list[PreparedAsset] = field(default_factory=list)
    files: list[PreparedFile] = field(default_factory=list)
    ignored_files: list[str] = field(default_factory=list)


@dataclass
class PreparedPlan:
    resolution: ResolutionPlan
    packages: list[PreparedPackage]
    work_dir: Path
