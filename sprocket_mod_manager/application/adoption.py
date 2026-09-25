from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..domain.errors import ModManagerError, ScanError
from ..domain.models import PreparedFile, RegistryPackage, ReleaseAsset, ReleaseInfo
from ..domain.registry import Registry
from ..domain.semver import Version
from ..infrastructure.dll_metadata import DllMetadata, flush_metadata_cache, read_cached_metadata
from ..infrastructure.github import GitHubClient
from ..infrastructure.installer import Installer
from ..infrastructure.mod_toggle import direct_files
from ..infrastructure.release_checksums import release_asset_sha256
from ..infrastructure.scanner import PackageScanner
from ..utilities.checksums import sha256_file
from ..utilities.dependencies import dependencies_for_release
from ..utilities.package_paths import file_type_namespace, validate_supply_target
from .identifiers import IDENTIFIERS, DetectedRuntime, ModDirectory, ModIdentifier, scan_targets

LOGGER = logging.getLogger(__name__)

REASON_DECLARED_ID = "declared-id"
REASON_REPOSITORY = "repository"

# 加载器把游戏指向自己的代理：游戏根目录里出现的就是这些名字。认领时只登记**当下存在**的那几个，
# 卸载只删内容与登记摘要仍然一致的那份。
LOADER_PROXY_FILES = ("version.dll", "winhttp.dll", "doorstop_config.ini")


@dataclass(frozen=True)
class RegistryMatch:
    package_id: str
    reason: str


def normalize_repository(value: str | None) -> str:
    """把 `owner/repo`、`https://github.com/owner/repo`、`git@github.com:owner/repo.git` 归一成小写 `owner/repo`。"""
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if text.casefold().endswith(".git"):
        text = text[:-4]
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if text.casefold().startswith(prefix):
            text = text[len(prefix):]
            break
    return text.strip("/").casefold()


def declared_id(metadata: DllMetadata) -> str:
    return str(metadata.sprocket.get("id", "") or "").strip()


def repository_of(metadata: DllMetadata) -> str:
    return str(metadata.sprocket.get("repository", "") or "").strip()


def declared_version_of(metadata: DllMetadata) -> str:
    """DLL 自报的版本：MelonInfo 优先，用户库没有 MelonInfo 时用程序集/文件版本。"""
    for value in (metadata.melon_version, metadata.assembly_version, metadata.file_version):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def matches_release_version(declared: str, release: Version) -> bool:
    """DLL 自报版本与发布版本是否同一版。

    .NET 程序集版本常写成四段（`0.2.2.0`），第四段是修订号：为 0 时与三段标签同版；
    非 0 的构建可能与那个标签不同源，所以不算同版。
    """
    text = str(declared or "").strip()
    if not text:
        return False
    parts = text.split(".")
    if len(parts) == 4 and parts[3].isdigit():
        if int(parts[3]) != 0:
            return False
        text = ".".join(parts[:3])
    try:
        return Version.parse(text) == release
    except ValueError:
        return False


def loader_package_for(
        registry: Registry,
        identifier: ModIdentifier,
        detected: DetectedRuntime,
) -> RegistryPackage | None:
    """检测到的这个运行时属于哪个加载器包。

    候选是「供给这个标识符名字空间」的加载器，位置由检测到的 `home` 定：同一个名字空间可以有
    多个供给者（原生加载器与把目录重新安家的桥接加载器），`MLLoader/Mods` 只可能来自那座桥。
    """
    home = detected.home.strip("/")
    for package in registry.packages:
        if not package.is_loader:
            continue
        for file_type, target in package.supply.items():
            if file_type_namespace(file_type) != identifier.namespace:
                continue
            mod_type = identifier.type_for(file_type)
            if mod_type is None:
                continue
            try:
                resolved = validate_supply_target(target).as_posix()
            except ScanError:
                continue
            expected = f"{home}/{mod_type.directory}" if home else mod_type.directory
            if resolved == expected:
                return package
    return None


def match_package_by_metadata(
        metadata: DllMetadata,
        packages: tuple[RegistryPackage, ...] | list[RegistryPackage],
) -> RegistryMatch | None:
    """只用 DLL 内置的两种身份信号匹配 Registry 包：

    1. ``Sprocket.Mod.Id`` == 包 id；
    2. ``Sprocket.Mod.Repository`` == 包 repository。

    故意不做程序集名 / 文件名猜测——那些猜测会认错包；确切的兜底交给 SHA-256 路径
    （``ExistingModsAdopter._digest_candidates``）。
    """
    if not packages:
        return None

    by_id = {package.id.casefold(): package for package in packages}
    declared = declared_id(metadata)
    if declared and declared.casefold() in by_id:
        return RegistryMatch(by_id[declared.casefold()].id, REASON_DECLARED_ID)

    repository = normalize_repository(repository_of(metadata))
    if repository:
        for package in packages:
            if normalize_repository(package.repository) == repository:
                return RegistryMatch(package.id, REASON_REPOSITORY)
    return None


@dataclass(frozen=True)
class AdoptionRecord:
    package_id: str
    name: str
    version: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class _Candidate:
    package: RegistryPackage
    release: ReleaseInfo
    assets: tuple[ReleaseAsset, ...]
    files: tuple[PreparedFile, ...]


class ExistingModsAdopter:
    def __init__(self, github: GitHubClient, installer: Installer):
        self.github = github
        self.installer = installer
        self.scanner = PackageScanner()

    def adopt(self, registry: Registry, game_dir: Path) -> tuple[AdoptionRecord, ...]:
        game_dir = self.installer.validate_game_dir(game_dir)
        state = self.installer.state_store.load()
        installed_ids = tuple(state["packages"])
        managed_paths = {relative.casefold() for relative in state["files"]}
        # 扫描目录与目标目录必须来自同一处：活跃标识符的目录表，即已装供给者的 `supply`
        # 加上磁盘上检测到的运行时。加载器只在磁盘上、记录里没有时，它的供给表同样给出
        # 目录（`melonloader:mod` -> `Mods`），认领不会因为记录为空而空转。
        targets = scan_targets(game_dir, registry.packages, installed_ids)
        self.scanner = PackageScanner(
            {
                directory.type.id: PurePosixPath(directory.path)
                for _identifier, directory in targets
            }
        )
        local_files = tuple(
            self._local_dlls(registry, game_dir, managed_paths, installed_ids, targets)
        )
        adopted = list(self._adopt_mods(registry, state["packages"], game_dir, local_files))
        adopted.extend(self._adopt_detected_loaders(registry, game_dir, state["packages"]))
        # 认领也会解析 DLL 元数据（`read_cached_metadata`）；这里自己落盘，不指望调用方记得 flush。
        flush_metadata_cache()
        return tuple(adopted)

    def adopt_detected_loader(
            self,
            registry: Registry,
            game_dir: Path,
            package_id: str,
    ) -> tuple[AdoptionRecord, ...]:
        """把磁盘上检测到的这一个加载器写进记录；别的加载器不动。

        卸载一个磁盘上有、记录里没有的加载器之前先用它登记：交还整棵树与代理文件需要记录里
        那份顶层条目清单，不登记就没有可交还的东西。
        """
        game_dir = self.installer.validate_game_dir(game_dir)
        state = self.installer.state_store.load()
        adopted = tuple(
            self._adopt_detected_loaders(
                registry, game_dir, state["packages"], only=package_id
            )
        )
        if adopted:
            flush_metadata_cache()
        return adopted

    def _adopt_mods(
            self,
            registry: Registry,
            recorded: dict[str, Any],
            game_dir: Path,
            local_files: tuple[Path, ...],
    ) -> Iterator[AdoptionRecord]:
        """按 DLL 内置身份或发布摘要认领模组；一个 DLL 都没扫到就什么都不做。"""
        if not local_files:
            return
        by_name: dict[str, list[Path]] = {}
        for path in local_files:
            by_name.setdefault(path.name.casefold(), []).append(path)
        digest_cache: dict[Path, str] = {}
        candidates: list[_Candidate] = []
        for package in registry.packages:
            if package.id in recorded:
                continue
            if package.is_loader:
                # 加载器不是模组：它的载荷是整棵树，身份与版本由标识符从运行时读出，
                # 认领路径在 `_adopt_detected_loaders`，这里不为它拉发布列表。
                continue
            matches = self._package_candidates(
                package,
                game_dir,
                local_files,
                by_name,
                digest_cache,
            )
            if len(matches) == 1:
                candidates.append(matches[0])

        claims = Counter(
            file.target.casefold()
            for candidate in candidates
            for file in candidate.files
        )
        for candidate in candidates:
            if any(claims[file.target.casefold()] != 1 for file in candidate.files):
                continue
            dependency_ids = tuple(
                item["id"]
                for item in dependencies_for_release(candidate.package, candidate.release)
            )
            if not self.installer.adopt(
                    candidate.package,
                    candidate.release,
                    candidate.files,
                    candidate.assets,
                    dependency_ids,
                    game_dir,
            ):
                continue
            yield AdoptionRecord(
                package_id=candidate.package.id,
                name=candidate.package.name,
                version=str(candidate.release.version),
                files=tuple(file.target for file in candidate.files),
            )

    def _adopt_detected_loaders(
            self,
            registry: Registry,
            game_dir: Path,
            recorded: dict[str, Any],
            *,
            only: str | None = None,
    ) -> list[AdoptionRecord]:
        """把磁盘上已在场、记录里没有的加载器写进记录。

        版本取标识符从运行时自身读到的那个真值，不由包的最新发布顶替；发布出处只查索引自带的
        发布数据，所以这条路径**不联网**。检测到的版本在索引里没有对应发布（注册表没见过这个
        构建）时仍然认领：记录里如实写检测到的版本，不写 tag 与 release id，也不编资产 —— 界面
        因此显示"装的是这版"而不是"装的是某个索引里的版本"。

        `only` 只认领这一个包：调用方要交还某个具体加载器时，别的加载器不该被顺带登记。
        """
        adopted: list[AdoptionRecord] = []
        for identifier in IDENTIFIERS:
            detected = identifier.detect(game_dir)
            if detected is None:
                continue
            package = loader_package_for(registry, identifier, detected)
            if package is None or package.id in recorded:
                continue
            if only is not None and package.id != only:
                continue
            directories = tuple(
                path
                for path in identifier.runtime_paths(detected)
                if (game_dir / path).is_dir()
            )
            if not directories:
                LOGGER.warning(
                    "loader %s is detected but its runtime directory is missing under %s",
                    package.id,
                    game_dir,
                )
                continue
            payload_files = tuple(
                (name, sha256_file(game_dir / name))
                for name in LOADER_PROXY_FILES
                if (game_dir / name).is_file()
            )
            release = self._indexed_release(package, detected.version)
            version = str(release.version) if release is not None else detected.version
            if release is None:
                LOGGER.warning(
                    "loader %s detected at version %r, which no indexed release matches; "
                    "recording the detected version",
                    package.id,
                    detected.version,
                )
            if not self.installer.adopt_loader(
                    package,
                    game_dir,
                    version=version,
                    directories=directories,
                    payload_files=payload_files,
                    dependencies=tuple(
                        item["id"]
                        for item in dependencies_for_release(package, release)
                    ) if release is not None else (),
                    tag=release.tag if release is not None else "",
                    release_id=release.id if release is not None else None,
            ):
                continue
            adopted.append(
                AdoptionRecord(
                    package_id=package.id,
                    name=package.name,
                    version=version,
                    files=directories + tuple(name for name, _digest in payload_files),
                )
            )
        return adopted

    @staticmethod
    def _indexed_release(package: RegistryPackage, version: str) -> ReleaseInfo | None:
        """索引自带的发布数据里与检测到的版本同版的那条；没有就是 `None`。"""
        for release in package.releases or ():
            if matches_release_version(version, release.version):
                return release
        return None

    def _package_candidates(
            self,
            package: RegistryPackage,
            game_dir: Path,
            local_files: tuple[Path, ...],
            by_name: dict[str, list[Path]],
            digest_cache: dict[Path, str],
    ) -> tuple[_Candidate, ...]:
        """先按 DLL 内置身份认领，失败再退回 SHA-256 比对。"""
        by_metadata = self._metadata_candidates(package, game_dir, local_files)
        if by_metadata:
            return by_metadata
        return self._digest_candidates(package, game_dir, by_name, digest_cache)

    def _metadata_candidates(
            self,
            package: RegistryPackage,
            game_dir: Path,
            local_files: tuple[Path, ...],
    ) -> tuple[_Candidate, ...]:
        """`Sprocket.Mod.Id` / `Sprocket.Mod.Repository` 命中即认领。

        版本取自 DLL 自报版本（见 `declared_version_of`），并据此挑发布版本；认领是"断言归属"，
        不是校验字节，所以这里不需要（也不应该）假装比对过摘要。
        """
        matched_files: list[PreparedFile] = []
        declared_version = ""
        for path in local_files:
            try:
                metadata = read_cached_metadata(path)
            except (ModManagerError, OSError):
                continue
            if match_package_by_metadata(metadata, (package,)) is None:
                continue
            try:
                files, ignored = self.scanner.scan(package, path, game_dir)
            except ModManagerError as exc:
                # 身份已经对上、目标却算不出来：这条认领被放弃，日志里必须留下原因，
                # 否则磁盘上明明认得出的模组会静默地留在"本地"。
                LOGGER.warning("cannot adopt %s from %s: %s", package.id, path.name, exc)
                continue
            target = self._relative_target(game_dir, path)
            if ignored or len(files) != 1 or files[0].target.casefold() != target.casefold():
                LOGGER.warning(
                    "cannot adopt %s: %s is not where its install rule puts it",
                    package.id,
                    target,
                )
                continue
            matched_files.append(files[0])
            if not declared_version:
                declared_version = declared_version_of(metadata)

        if not matched_files:
            return ()

        release = self._release_for_version(package, declared_version)
        if release is None:
            LOGGER.warning("cannot adopt %s: the registry has no release to record", package.id)
            return ()
        assets = self.github.install_assets(package, release)
        if not assets:
            LOGGER.warning(
                "cannot adopt %s: release %s has no installable asset", package.id, release.version
            )
            return ()
        return (_Candidate(package, release, assets, tuple(matched_files)),)

    def _release_for_version(self, package: RegistryPackage, version: str) -> ReleaseInfo | None:
        """自报版本命中的那个发布；一个都不命中时退回最新 release。

        认领断言的是归属，不是字节：本地构建比所有发布都旧、或版本读不出来时，仍按最新发布
        登记，让这个包重新可管理。文件内容不动，完整性判定随后拿磁盘内容与**全部**发布版本的
        资产比对 —— 对得上旧版本资产的构建照常算正常，对不上任何发布资产的构建在已安装列表里
        标成损坏。
        """
        try:
            releases = self.github.releases(package)
        except (ModManagerError, OSError, ValueError):
            return None
        for release in releases:
            if matches_release_version(version, release.version):
                return release
        return releases[0] if releases else None

    def _digest_candidates(
            self,
            package: RegistryPackage,
            game_dir: Path,
            by_name: dict[str, list[Path]],
            digest_cache: dict[Path, str],
    ) -> tuple[_Candidate, ...]:
        candidates: list[_Candidate] = []
        try:
            releases = self.github.releases(package)
        except (ModManagerError, OSError, ValueError):
            return ()
        for release in releases:
            assets = self.github.install_assets(package, release)
            if not assets or any(Path(asset.name).suffix.casefold() != ".dll" for asset in assets):
                continue
            matched_files: list[PreparedFile] = []
            valid = True
            for asset in assets:
                expected = release_asset_sha256(asset)
                if expected is None:
                    valid = False
                    break
                matches = []
                for path in by_name.get(Path(asset.name).name.casefold(), ()):
                    actual = digest_cache.get(path)
                    if actual is None:
                        try:
                            actual = sha256_file(path)
                        except OSError:
                            continue
                        digest_cache[path] = actual
                    if actual != expected:
                        continue
                    try:
                        files, ignored = self.scanner.scan(package, path, game_dir)
                    except ModManagerError as exc:
                        LOGGER.warning(
                            "cannot adopt %s from %s: %s", package.id, path.name, exc
                        )
                        continue
                    target = self._relative_target(game_dir, path)
                    if not ignored and len(files) == 1 and files[0].target.casefold() == target.casefold():
                        matches.append(files[0])
                if len(matches) != 1:
                    valid = False
                    break
                matched_files.append(matches[0])
            if valid and len({file.target.casefold() for file in matched_files}) == len(matched_files):
                candidates.append(_Candidate(package, release, assets, tuple(matched_files)))
        return tuple(candidates)

    @staticmethod
    def _relative_target(game_dir: Path, path: Path) -> str:
        return path.resolve().relative_to(game_dir.resolve()).as_posix()

    @classmethod
    def _local_dlls(
            cls,
            registry: Registry,
            game_dir: Path,
            managed_paths: set[str],
            installed_ids: tuple[str, ...],
            targets: tuple[tuple[ModIdentifier, ModDirectory], ...] | None = None,
    ) -> Iterator[Path]:
        """活跃标识符的目录下 **1 层** 内、不在安装记录里的真实 DLL（符号链接不算）。"""
        if targets is None:
            targets = scan_targets(game_dir, registry.packages, installed_ids)
        for _identifier, directory in targets:
            root = game_dir / directory.path
            if not root.is_dir():
                continue
            for path in direct_files(root):
                try:
                    if (
                            path.is_symlink()
                            or path.suffix.casefold() != ".dll"
                    ):
                        continue
                    relative = cls._relative_target(game_dir, path)
                except (OSError, ValueError):
                    continue
                if relative.casefold() not in managed_paths:
                    yield path
