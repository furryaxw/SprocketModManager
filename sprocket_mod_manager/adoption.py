from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .errors import ModManagerError
from .github import GitHubClient
from .hashing import release_asset_sha256, sha256_file
from .installer import Installer
from .models import PreparedFile, RegistryPackage, ReleaseAsset, ReleaseInfo
from .registry import Registry
from .scanner import PackageScanner
from .solver import dependencies_for_release


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
        return tuple(adopted)

    def _package_candidates(
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
        for root_name in ("Mods", "UserLibs"):
            root = game_dir / root_name
            if not root.is_dir():
                continue
            try:
                for path in root.rglob("*"):
                    try:
                        if (
                            path.is_symlink()
                            or not path.is_file()
                            or path.suffix.casefold() != ".dll"
                        ):
                            continue
                        relative = cls._relative_target(game_dir, path)
                    except (OSError, ValueError):
                        continue
                    if relative.casefold() not in managed_paths:
                        yield path
            except OSError:
                continue
