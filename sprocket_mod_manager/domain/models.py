from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

from .semver import Version

ProgressCallback = Callable[[str], None]

# 包的种类：决定它用哪条安装线，以及它在目录里是什么。
MODFILE_KIND = "modfile"
MODLOADER_KIND = "modloader"
LOADERBRIDGE_KIND = "loaderbridge"
TRANSLATELOADER_KIND = "translateloader"
PATCH_KIND = "patch"
PACKAGE_KINDS = frozenset({
    MODFILE_KIND,
    MODLOADER_KIND,
    LOADERBRIDGE_KIND,
    TRANSLATELOADER_KIND,
    PATCH_KIND,
})
LOADER_KINDS = frozenset({
    MODLOADER_KIND,
    LOADERBRIDGE_KIND,
    TRANSLATELOADER_KIND,
    PATCH_KIND,
})
# `provides` 里的这个字面量表示「这条发布自己的版本」。
VERSION_TEMPLATE = "{version}"


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
    recommendations: tuple[str, ...] = ()
    featured: bool = False
    meta_url: str = ""
    releases: tuple["ReleaseInfo", ...] | None = None
    schema_version: int = 1
    kind: str = MODFILE_KIND
    # 加载器的供给表：可安装类型 -> 游戏根目录下的位置。非空即表示这个包供给某类文件。
    supply: dict[str, str] = field(default_factory=dict)
    # v2 的安装规则（按顺序匹配文件名/条目路径，给出类型）。v1 用 install.overrides。
    file_rules: tuple[dict[str, str], ...] = ()
    # 加载器自己的安装线（按顺序匹配条目路径，给出游戏根目录下的位置）。
    payload_rules: tuple[dict[str, str], ...] = ()
    # 这个包向别处提供的能力：能力 id -> 版本字符串（`{version}` 表示自己的发布版本）。
    provides: dict[str, str] = field(default_factory=dict)

    @property
    def install_mode(self) -> str:
        return str(self.install.get("mode", "standard"))

    def replace_types(self) -> tuple[str, ...]:
        """这个包整体接管的安装类型（去重保序）；空元组表示它只按文件打补丁。

        接管一个类型＝安装时备份并清空该类型的供给目录再落文件，卸载时整目录还原。
        """
        seen: dict[str, None] = {}
        for file_type in self.install.get("replace", ()) or ():
            seen.setdefault(str(file_type), None)
        return tuple(seen)

    @property
    def source(self) -> dict[str, Any]:
        """二进制来源；没写就是包自己的 GitHub Releases。"""
        raw = self.release.get("source")
        return dict(raw) if isinstance(raw, dict) else {"type": "github"}

    def asset_hosts(self) -> tuple[str, ...]:
        """下载地址允许的 HTTPS 主机。

        默认只允许这个包自己的 GitHub Releases；声明成外部来源的包可以在 `release.source.hosts`
        里另外放行几个主机（例如加载器的官方构建站），除此之外一律拒绝。
        """
        source = self.source
        if str(source.get("type", "github")) == "external":
            return tuple(str(host).casefold() for host in source.get("hosts", ()))
        return ("github.com",)

    @property
    def is_modloader(self) -> bool:
        """是不是基础运行时：只有它出现在客户端的加载器页，也只有它能声明外部来源。"""
        return self.kind == MODLOADER_KIND

    @property
    def is_loader(self) -> bool:
        """是不是某类加载器：加载器自身用 `install.payload` 安装。"""
        return self.kind in LOADER_KINDS

    @property
    def uses_payload(self) -> bool:
        return bool(self.payload_rules)

    def declared_capabilities(self) -> dict[str, str]:
        """显式写下的 `provides`（不含隐含的自己那一项）。"""
        return dict(self.provides)

    def capabilities(self) -> dict[str, str]:
        """这个包提供的能力：显式 `provides`，没写就是它自己的包 id。

        `{version}` 交给调用方按实际发布版本替换 —— 这个包不知道会装哪一版。
        """
        return dict(self.provides) if self.provides else {self.id: VERSION_TEMPLATE}

    def declared_types(self) -> tuple[str, ...]:
        """静态规则覆盖到的类型（去重保序）。据此自动推导需要哪些加载器。"""
        seen: dict[str, None] = {}
        for rule in self.file_rules:
            file_type = str(rule.get("type", ""))
            if file_type:
                seen.setdefault(file_type, None)
        return tuple(seen)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RegistryPackage":
        raw_releases = data.get("releases")
        if raw_releases is not None and not isinstance(raw_releases, list):
            raise TypeError("releases must be a list")
        raw_recommendations = data.get("recommendations", ())
        if not isinstance(raw_recommendations, (list, tuple)):
            raise TypeError("recommendations must be a list")
        if not all(isinstance(item, str) and item for item in raw_recommendations):
            raise TypeError("recommendations must contain package ids")
        if len(raw_recommendations) != len(set(raw_recommendations)):
            raise ValueError("recommendations must not contain duplicates")
        raw_featured = data.get("featured", False)
        if not isinstance(raw_featured, bool):
            raise TypeError("featured must be a boolean")
        release_rules = dict(data.get("release", {}))
        install_rules = dict(data.get("install", {}))
        raw_source = release_rules.get("source")
        external = (
            isinstance(raw_source, dict) and str(raw_source.get("type", "github")) == "external"
        )
        # 外部来源只看主机白名单；GitHub 来源还要求 URL 确实落在这个仓库自己的 releases 下。
        asset_hosts = (
            tuple(str(host).casefold() for host in raw_source.get("hosts", ()))
            if external
            else None
        )
        releases = (
            None
            if raw_releases is None
            else tuple(
                ReleaseInfo.from_dict(item, repository=str(data["repository"]), hosts=asset_hosts)
                for item in raw_releases
            )
        )
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
        kind = str(data.get("kind", MODFILE_KIND))
        if kind not in PACKAGE_KINDS:
            raise ValueError(f"unknown package kind: {kind}")
        raw_provides = data.get("provides", {})
        if not isinstance(raw_provides, dict):
            raise TypeError("provides must be an object")
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
            install=install_rules,
            category=data.get("category", "other"),
            tags=tuple(data.get("tags", ())),
            recommendations=tuple(raw_recommendations),
            featured=raw_featured,
            meta_url=data.get("meta_url", ""),
            releases=releases,
            schema_version=int(data.get("schema_version", 1)),
            kind=kind,
            supply={
                str(file_type): str(target)
                for file_type, target in dict(data.get("supply", {}) or {}).items()
            },
            file_rules=tuple(
                {
                    "match": str(rule["match"]),
                    "type": str(rule["type"]),
                    **({"subpath": str(rule["subpath"])} if rule.get("subpath") else {}),
                    **({"layout": str(rule["layout"])} if rule.get("layout") else {}),
                }
                for rule in install_rules.get("files", ())
                if isinstance(rule, dict) and "match" in rule and "type" in rule
            ),
            payload_rules=tuple(
                {
                    "match": str(rule["match"]),
                    "target": str(rule["target"]),
                    **({"subpath": str(rule["subpath"])} if rule.get("subpath") else {}),
                    **({"layout": str(rule["layout"])} if rule.get("layout") else {}),
                }
                for rule in install_rules.get("payload", ())
                if isinstance(rule, dict) and "match" in rule and "target" in rule
            ),
            provides={
                str(capability_id): str(version)
                for capability_id, version in raw_provides.items()
            },
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
    def from_dict(
            cls,
            data: dict[str, Any],
            *,
            repository: str = "",
            hosts: tuple[str, ...] | None = None,
    ) -> "ReleaseAsset":
        if not isinstance(data, dict):
            raise TypeError("release asset must be an object")
        download_url = str(data["download_url"])
        parsed = urlparse(download_url)
        if hosts:
            allowed = {host.casefold() for host in hosts}
            if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in allowed:
                raise ValueError(f"release asset host is not allowed: {download_url}")
        elif repository:
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
    # 索引里这条 release 的兼容声明：对若干能力（游戏那根是 `hamish.sprocket`，加载器那些
    # 是各自的能力 id）的区间，以及它是自己写的还是从更早的 release 继承来的。
    dependencies: tuple[dict[str, str], ...] = ()
    compatibility: dict[str, Any] | None = None

    @classmethod
    def from_dict(
            cls,
            data: dict[str, Any],
            *,
            repository: str = "",
            hosts: tuple[str, ...] | None = None,
    ) -> "ReleaseInfo":
        if not isinstance(data, dict):
            raise TypeError("release must be an object")
        page_url = str(data.get("page_url", ""))
        parsed = urlparse(page_url)
        if hosts:
            if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {
                host.casefold() for host in hosts
            }:
                raise ValueError(f"release page host is not allowed: {page_url}")
        elif repository:
            expected_prefix = f"/{repository}/releases/tag/".casefold()
            if (
                    parsed.scheme != "https"
                    or (parsed.hostname or "").casefold() != "github.com"
                    or not parsed.path.casefold().startswith(expected_prefix)
            ):
                raise ValueError(f"invalid embedded release page URL: {page_url}")
        raw_compatibility = data.get("compatibility")
        compatibility = (
            dict(raw_compatibility) if isinstance(raw_compatibility, dict) else None
        )
        return cls(
            id=int(data.get("id", 0)),
            tag=str(data["tag"]),
            version=Version.parse(str(data["version"])),
            prerelease=bool(data.get("prerelease")),
            published_at=str(data.get("published_at", "")),
            assets=tuple(
                ReleaseAsset.from_dict(item, repository=repository, hosts=hosts)
                for item in data.get("assets", ())
            ),
            page_url=page_url,
            dependencies=tuple(
                {
                    "id": str(item["id"]),
                    "version": str(item["version"]),
                }
                for item in data.get("dependencies", ())
                if isinstance(item, dict) and "id" in item and "version" in item
            ),
            compatibility=compatibility,
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
    # 类型 -> 游戏根目录下的相对目录（供给者决定）。安装时「整体接管」的类型靠它定位目录。
    install_directories: dict[str, PurePosixPath] = field(default_factory=dict)
