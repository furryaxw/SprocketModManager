from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RegistryPackage":
        return cls(
            id=data["id"],
            name=data["name"],
            authors=tuple(data.get("authors", ())),
            repository=data["repository"],
            license=data.get("license", ""),
            display_name=dict(data.get("display_name", {})),
            description=dict(data.get("description", {})),
            release=dict(data.get("release", {})),
            dependencies=tuple(dict(item) for item in data.get("dependencies", ())),
            install=dict(data.get("install", {})),
            category=data.get("category", "other"),
            tags=tuple(data.get("tags", ())),
            meta_url=data.get("meta_url", ""),
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


@dataclass(frozen=True)
class ReleaseInfo:
    id: int
    tag: str
    version: Version
    prerelease: bool
    published_at: str
    assets: tuple[ReleaseAsset, ...]


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
