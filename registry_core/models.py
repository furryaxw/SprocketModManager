from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

from .semver import Version, satisfies

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


def rule_applies_to_version(rule: dict[str, str], version: str) -> bool:
    """这条安装规则管不管这一版。

    规则的 `when` 是**这个包自己**的版本区间（与依赖那一项同一套写法），省略表示不限版本。
    一个仓库两条发布线时，两条线各写一条规则、把分界版本写进 `when` 就能各装各的目录。
    给不出可读的版本（例如 DLL 没自报版本、调用方还没挑出版本）时，只有不限版本的规则算命中 ——
    那样扫描会退回按 DLL 自身的分类落位，比拿另一条线的规则硬套强。
    """
    when = str(rule.get("when") or "").strip()
    if not when or when == "*":
        return True
    text = str(version or "").strip()
    if not text:
        return False
    try:
        return satisfies(text, when)
    except (IndexError, ValueError):
        return False


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
    # 这条读数读不出来或自相矛盾的地方。非空表示它在目录里标成不可用，但照常列出来。
    issues: tuple[str, ...] = ()

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

    def file_rules_for(self, version: str) -> tuple[dict[str, str], ...]:
        """命中这一版的 `install.files` 规则，按声明顺序（先写的先匹配）。"""
        return tuple(rule for rule in self.file_rules if rule_applies_to_version(rule, version))

    def payload_rules_for(self, version: str) -> tuple[dict[str, str], ...]:
        """命中这一版的 `install.payload` 规则，按声明顺序。"""
        return tuple(rule for rule in self.payload_rules if rule_applies_to_version(rule, version))

    def declared_types(self) -> tuple[str, ...]:
        """全部规则覆盖到的类型（去重保序）：给「哪些加载器能供给这个包」这类与版本无关的判定用。"""
        seen: dict[str, None] = {}
        for rule in self.file_rules:
            file_type = str(rule.get("type", ""))
            if file_type:
                seen.setdefault(file_type, None)
        return tuple(seen)

    def declared_types_for(self, version: str) -> tuple[str, ...]:
        """这一版规则覆盖到的类型（去重保序）。据此自动推导这一版需要哪些加载器。"""
        seen: dict[str, None] = {}
        for rule in self.file_rules_for(version):
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
                ReleaseInfo.from_dict(
                    item, repository=str(data.get("repository", "")), hosts=asset_hosts
                )
                for item in raw_releases
            )
        )
        if releases is not None and release_rules.get("version_pattern") is not None:
            try:
                version_pattern = re.compile(str(release_rules["version_pattern"]))
            except re.error as exc:
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
            repository=str(data.get("repository", "")),
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
                    # `when` 是这条规则只对哪些自有版本生效：漏掉它，两条发布线的规则会同时命中。
                    **({"when": str(rule["when"])} if rule.get("when") else {}),
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
                    **({"when": str(rule["when"])} if rule.get("when") else {}),
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

    @classmethod
    def from_invalid(
            cls,
            data: dict[str, Any],
            reason: str,
            *,
            fallback_id: str,
    ) -> "RegistryPackage":
        """一条读不出来的条目：留一份能显示的最小读数，原因挂在 `issues` 上。

        目录要把坏条目也列出来并说明原因，所以这里不抛错；拿不到的字段给空值。
        """
        raw_id = data.get("id")
        package_id = raw_id if isinstance(raw_id, str) and raw_id else fallback_id
        raw_name = data.get("name")
        name = raw_name if isinstance(raw_name, str) and raw_name else package_id

        def localized(field: str) -> dict[str, str]:
            value = data.get(field)
            if not isinstance(value, dict):
                return {}
            return {
                str(key): str(text)
                for key, text in value.items()
                if isinstance(key, str) and isinstance(text, str)
            }

        def texts(field: str) -> tuple[str, ...]:
            value = data.get(field)
            if not isinstance(value, (list, tuple)):
                return ()
            return tuple(str(item) for item in value if isinstance(item, str))

        def mapping(field: str) -> dict[str, Any]:
            value = data.get(field)
            return dict(value) if isinstance(value, dict) else {}

        try:
            schema_version = int(data.get("schema_version", 1))
        except (TypeError, ValueError):
            schema_version = 1
        return cls(
            id=package_id,
            name=name,
            authors=texts("authors"),
            repository=str(data.get("repository") or ""),
            license=str(data.get("license") or ""),
            display_name=localized("display_name"),
            description=localized("description"),
            release=mapping("release"),
            dependencies=(),
            install=mapping("install"),
            category=str(data.get("category") or "other"),
            tags=texts("tags"),
            meta_url=str(data.get("meta_url") or ""),
            schema_version=schema_version,
            kind=str(data.get("kind") or MODFILE_KIND),
            issues=(reason,),
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
            name=str(data.get("name", "")),
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
        # 只有写下的页面地址才判断主机；缺失时没有可判断的内容。
        page_url = str(data.get("page_url", ""))
        if page_url:
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
            tag=str(data.get("tag", "")),
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
