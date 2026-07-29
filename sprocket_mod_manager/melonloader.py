from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pefile

from .errors import DownloadError, InstallError
from .github import GITHUB_RELEASE_CACHE_SECONDS, HttpClient
from .hashing import sha256_file
from .installer import Installer, sprocket_is_running
from .models import ProgressCallback, ReleaseAsset
from .scanner import (
    MAX_ARCHIVE_FILES,
    MAX_ARCHIVE_FILE_BYTES,
    MAX_ARCHIVE_TOTAL_BYTES,
    MAX_COMPRESSION_RATIO,
    validate_relative_path,
)
from .semver import Version


MELONLOADER_REPOSITORY = "LavaGang/MelonLoader"
MELONLOADER_ASSET_NAME = "MelonLoader.x64.zip"
MELONLOADER_API_URL = (
    "https://api.github.com/repos/LavaGang/MelonLoader/releases/latest"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class MelonLoaderInstallation:
    installed: bool
    version: Version | None


@dataclass(frozen=True)
class MelonLoaderRelease:
    tag: str
    version: Version
    page_url: str
    asset: ReleaseAsset


@dataclass(frozen=True)
class MelonLoaderInstallResult:
    release: MelonLoaderRelease
    files_installed: int
    sha256: str
    publisher_verified: bool


def _file_version(path: Path) -> Version | None:
    image: pefile.PE | None = None
    try:
        image = pefile.PE(str(path))
        fixed = image.VS_FIXEDFILEINFO[0]
        major = fixed.FileVersionMS >> 16
        minor = fixed.FileVersionMS & 0xFFFF
        patch = fixed.FileVersionLS >> 16
        return Version(major, minor, patch)
    except (AttributeError, IndexError, OSError, pefile.PEFormatError, ValueError):
        return None
    finally:
        if image is not None:
            image.close()


class MelonLoaderManager:
    def __init__(self, app_dir: Path, http: HttpClient):
        self.app_dir = app_dir
        self.http = http
        self._install_lock = threading.Lock()

    @staticmethod
    def detect(game_dir: Path) -> MelonLoaderInstallation:
        game_dir = game_dir.expanduser().resolve()
        proxy = game_dir / "version.dll"
        loader_root = game_dir / "MelonLoader"
        core_candidates = (
            sorted(loader_root.glob("net*/MelonLoader.dll"))
            if loader_root.is_dir()
            else []
        )
        legacy_core = loader_root / "MelonLoader.dll"
        if legacy_core.is_file():
            core_candidates.append(legacy_core)

        installed = proxy.is_file() and bool(core_candidates)
        if not installed:
            return MelonLoaderInstallation(False, None)
        version = next(
            (candidate for path in core_candidates if (candidate := _file_version(path)) is not None),
            None,
        )
        return MelonLoaderInstallation(True, version)

    def latest_release(self, *, refresh: bool = False) -> MelonLoaderRelease:
        record = self.http.get_json(
            MELONLOADER_API_URL,
            cache_seconds=0 if refresh else GITHUB_RELEASE_CACHE_SECONDS,
        )
        if not isinstance(record, dict) or record.get("draft") or record.get("prerelease"):
            raise DownloadError("invalid latest MelonLoader Release response")

        tag = str(record.get("tag_name", "")).strip()
        version_text = tag[1:] if tag[:1].casefold() == "v" else tag
        try:
            version = Version.parse(version_text)
        except ValueError as exc:
            raise DownloadError(f"latest MelonLoader Release tag is not SemVer: {tag or '-'}") from exc

        page_url = str(record.get("html_url", ""))
        parsed_page = urlparse(page_url)
        expected_page = f"/{MELONLOADER_REPOSITORY}/releases/tag/".casefold()
        if (
            parsed_page.scheme != "https"
            or (parsed_page.hostname or "").casefold() != "github.com"
            or not parsed_page.path.casefold().startswith(expected_page)
        ):
            raise DownloadError(f"invalid MelonLoader Release page URL: {page_url or '-'}")

        assets = record.get("assets")
        if not isinstance(assets, list):
            raise DownloadError("latest MelonLoader Release has no asset list")
        matches = [
            item
            for item in assets
            if isinstance(item, dict)
            and str(item.get("name", "")).casefold() == MELONLOADER_ASSET_NAME.casefold()
        ]
        if len(matches) != 1:
            raise DownloadError(
                f"latest MelonLoader Release must contain exactly one {MELONLOADER_ASSET_NAME} asset"
            )
        raw_asset = matches[0]
        asset = ReleaseAsset.from_dict(
            {
                "id": raw_asset.get("id", 0),
                "name": raw_asset.get("name", ""),
                "size": raw_asset.get("size", 0),
                "download_url": raw_asset.get("browser_download_url", ""),
                "digest": raw_asset.get("digest"),
                "updated_at": raw_asset.get("updated_at", ""),
            },
            repository=MELONLOADER_REPOSITORY,
        )
        if asset.size <= 0:
            raise DownloadError("MelonLoader Release asset has an invalid size")
        return MelonLoaderRelease(tag, version, page_url, asset)

    def status(
        self,
        game_dir: Path,
        *,
        include_latest: bool = True,
        refresh: bool = False,
    ) -> tuple[MelonLoaderInstallation, MelonLoaderRelease | None]:
        game_dir = Installer.validate_game_dir(game_dir)
        installation = self.detect(game_dir)
        release = self.latest_release(refresh=refresh) if include_latest else None
        return installation, release

    def install(
        self,
        game_dir: Path,
        *,
        refresh: bool = False,
        progress: ProgressCallback | None = None,
    ) -> MelonLoaderInstallResult:
        if not self._install_lock.acquire(blocking=False):
            raise InstallError("a MelonLoader installation is already running")
        try:
            game_dir = Installer.validate_game_dir(game_dir)
            if sprocket_is_running():
                raise InstallError("Sprocket is running; close the game before installing MelonLoader")
            release = self.latest_release(refresh=refresh)
            working_root = self.app_dir / "melonloader"
            working_root.mkdir(parents=True, exist_ok=True)
            work_dir = Path(tempfile.mkdtemp(prefix="install-", dir=working_root))
            try:
                archive_path = work_dir / release.asset.name
                if progress:
                    progress(f"Downloading MelonLoader {release.version}")
                self.http.download(release.asset, archive_path, progress=progress)
                digest = sha256_file(archive_path)
                expected = self._expected_digest(release.asset)
                if expected and digest != expected:
                    raise DownloadError(
                        f"MelonLoader SHA-256 mismatch: expected {expected}, got {digest}"
                    )

                staging = work_dir / "staging"
                files = self._extract_archive(archive_path, staging)
                self._validate_payload(staging)
                if progress:
                    progress(f"Installing {len(files)} MelonLoader files")
                self._apply_files(staging, files, game_dir)
                return MelonLoaderInstallResult(
                    release=release,
                    files_installed=len(files),
                    sha256=digest,
                    publisher_verified=expected is not None,
                )
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)
        finally:
            self._install_lock.release()

    @staticmethod
    def _expected_digest(asset: ReleaseAsset) -> str | None:
        if not asset.digest:
            return None
        algorithm, separator, value = asset.digest.partition(":")
        if separator and algorithm.casefold() == "sha256" and _SHA256_RE.fullmatch(value):
            return value.casefold()
        raise DownloadError("MelonLoader Release asset has an invalid digest")

    @staticmethod
    def _extract_archive(archive_path: Path, staging: Path) -> list[Path]:
        try:
            archive = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise InstallError(f"invalid MelonLoader ZIP: {exc}") from exc

        files: list[Path] = []
        seen: set[str] = set()
        total_size = 0
        with archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_FILES:
                raise InstallError("MelonLoader ZIP contains too many entries")
            for member in members:
                try:
                    relative = validate_relative_path(member.filename)
                except Exception as exc:
                    raise InstallError(f"unsafe path in MelonLoader ZIP: {member.filename!r}") from exc
                identity = relative.as_posix().casefold().rstrip("/")
                if identity in seen:
                    raise InstallError(f"duplicate path in MelonLoader ZIP: {member.filename}")
                seen.add(identity)
                unix_mode = member.external_attr >> 16
                if unix_mode & 0o170000 == 0o120000:
                    raise InstallError(f"symbolic links are not allowed in MelonLoader ZIP: {member.filename}")
                if member.is_dir():
                    continue
                if member.flag_bits & 0x1:
                    raise InstallError(f"encrypted entries are not allowed in MelonLoader ZIP: {member.filename}")
                total_size += member.file_size
                if member.file_size > MAX_ARCHIVE_FILE_BYTES or total_size > MAX_ARCHIVE_TOTAL_BYTES:
                    raise InstallError("MelonLoader ZIP exceeds extraction size limits")
                if member.compress_size == 0 and member.file_size > 0:
                    raise InstallError(f"invalid compressed entry in MelonLoader ZIP: {member.filename}")
                if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                    raise InstallError(f"unsafe compression ratio in MelonLoader ZIP: {member.filename}")

                destination = staging / Path(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                files.append(destination)
        return files

    @staticmethod
    def _validate_payload(staging: Path) -> None:
        if not (staging / "version.dll").is_file():
            raise InstallError("MelonLoader ZIP does not contain version.dll at its root")
        loader_root = staging / "MelonLoader"
        if not loader_root.is_dir() or not any(loader_root.glob("net*/MelonLoader.dll")):
            raise InstallError("MelonLoader ZIP does not contain the expected loader runtime")

    def _apply_files(self, staging: Path, files: list[Path], game_dir: Path) -> None:
        transaction_root = self.app_dir / "transactions"
        transaction_root.mkdir(parents=True, exist_ok=True)
        transaction = Path(tempfile.mkdtemp(prefix="melonloader-", dir=transaction_root))
        backups: dict[Path, Path | None] = {}
        try:
            for source in files:
                relative = source.relative_to(staging)
                target = (game_dir / relative).resolve()
                try:
                    target.relative_to(game_dir)
                except ValueError as exc:
                    raise InstallError(f"MelonLoader target escapes the game directory: {relative}") from exc
                if target.exists() and not target.is_file():
                    raise InstallError(f"MelonLoader target is not a file: {relative}")
                self._backup(target, game_dir, transaction, backups)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.smm-{uuid.uuid4().hex}.tmp")
                try:
                    shutil.copy2(source, temporary)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
        except Exception:
            self._rollback(backups)
            raise
        finally:
            shutil.rmtree(transaction, ignore_errors=True)

    @staticmethod
    def _backup(
        target: Path,
        game_dir: Path,
        transaction: Path,
        backups: dict[Path, Path | None],
    ) -> None:
        if target in backups:
            return
        if not target.exists():
            backups[target] = None
            return
        relative = target.relative_to(game_dir)
        backup = transaction / "backup" / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        backups[target] = backup

    @staticmethod
    def _rollback(backups: dict[Path, Path | None]) -> None:
        for target, backup in reversed(list(backups.items())):
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
            except OSError:
                pass
