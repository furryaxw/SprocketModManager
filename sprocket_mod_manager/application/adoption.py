from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ..domain.errors import ModManagerError
from ..domain.models import PreparedFile, RegistryPackage, ReleaseAsset, ReleaseInfo
from ..domain.registry import Registry
from ..infrastructure.dll_metadata import DllMetadata, flush_metadata_cache, read_cached_metadata
from ..infrastructure.github import GitHubClient
from ..infrastructure.installer import Installer
from ..infrastructure.mod_toggle import direct_files
from ..infrastructure.release_checksums import release_asset_sha256
from ..infrastructure.scanner import PackageScanner
from ..utilities.checksums import sha256_file
from ..utilities.dependencies import dependencies_for_release

REASON_DECLARED_ID = "declared-id"
REASON_REPOSITORY = "repository"


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
        managed_paths = {relative.casefold() for relative in state["files"]}
        local_files = tuple(self._local_dlls(game_dir, managed_paths))
        if not local_files:
            return ()

        by_name: dict[str, list[Path]] = {}
        for path in local_files:
            by_name.setdefault(path.name.casefold(), []).append(path)
        digest_cache: dict[Path, str] = {}
        candidates: list[_Candidate] = []
        for package in registry.packages:
            if package.id in state["packages"]:
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
        adopted: list[AdoptionRecord] = []
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
            adopted.append(
                AdoptionRecord(
                    package_id=candidate.package.id,
                    name=candidate.package.name,
                    version=str(candidate.release.version),
                    files=tuple(file.target for file in candidate.files),
                )
            )
        # 认领也会解析 DLL 元数据（`read_cached_metadata`）；这里自己落盘，不指望调用方记得 flush。
        flush_metadata_cache()
        return tuple(adopted)

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

        版本取自 DLL 的 MelonInfo；匹配不到就记最新 release——认领是"断言归属"，不是校验字节，
        所以这里不需要（也不应该）假装比对过摘要。
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
            except ModManagerError:
                continue
            target = self._relative_target(game_dir, path)
            if ignored or len(files) != 1 or files[0].target.casefold() != target.casefold():
                continue
            matched_files.append(files[0])
            if not declared_version:
                declared_version = str(metadata.melon_version or "")

        if not matched_files:
            return ()

        release = self._release_for_version(package, declared_version)
        if release is None:
            return ()
        assets = self.github.install_assets(package, release)
        if not assets:
            return ()
        return (_Candidate(package, release, assets, tuple(matched_files)),)

    def _release_for_version(self, package: RegistryPackage, version: str) -> ReleaseInfo | None:
        try:
            releases = self.github.releases(package)
        except (ModManagerError, OSError, ValueError):
            return None
        if version:
            for release in releases:
                if str(release.version) == version:
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
                    except ModManagerError:
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
            game_dir: Path,
            managed_paths: set[str],
    ) -> Iterator[Path]:
        """`Mods` / `UserLibs` 下 **1 层** 内、不在安装记录里的真实 DLL（符号链接不算）。"""
        for root_name in ("Mods", "UserLibs"):
            root = game_dir / root_name
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
