from __future__ import annotations

import copy
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import InstallError
from .hashing import release_asset_sha256, sha256_file
from .models import (
    PreparedFile,
    PreparedPlan,
    ProgressCallback,
    RegistryPackage,
    ReleaseAsset,
    ReleaseInfo,
)
from .scanner import XUNITY_TRANSLATION_MODE, validate_target
from .state import StateStore


XUNITY_TRANSLATION_BACKUP_LIMIT = 5


def sprocket_is_running() -> bool:
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Sprocket.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    return "sprocket.exe" in stdout.casefold()


def _safe_game_path(game_dir: Path, relative: str) -> Path:
    target = validate_target(relative)
    root = game_dir.resolve()
    full = (root / Path(*target.parts)).resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise InstallError(f"install target escapes the game directory: {relative}") from exc
    return full


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_xunity_translation_path(relative: str) -> bool:
    parts = relative.replace("\\", "/").split("/")
    return bool(parts) and parts[0].casefold() == "autotranslator"


class Installer:
    def __init__(self, app_dir: Path, state: StateStore):
        self.app_dir = app_dir
        self.state_store = state

    @staticmethod
    def validate_game_dir(game_dir: Path) -> Path:
        resolved = game_dir.expanduser().resolve()
        if not resolved.is_dir() or not (resolved / "Sprocket.exe").is_file():
            raise InstallError(f"not a Sprocket game directory: {resolved}")
        return resolved

    @staticmethod
    def _incoming_files(prepared: PreparedPlan) -> dict[str, list[PreparedFile]]:
        incoming: dict[str, list[PreparedFile]] = {}
        for package in prepared.packages:
            for file in package.files:
                key = file.target.casefold()
                previous = incoming.get(key, [])
                if previous and previous[0].sha256 != file.sha256:
                    raise InstallError(f"install plan has a file conflict at {file.target}")
                previous.append(file)
                incoming[key] = previous
        return incoming

    def apply(
        self,
        prepared: PreparedPlan,
        game_dir: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> list[str]:
        game_dir = self.validate_game_dir(game_dir)
        if sprocket_is_running():
            raise InstallError("Sprocket is running; close the game before changing mod files")
        state = self.state_store.load()
        next_state = copy.deepcopy(state)
        incoming = self._incoming_files(prepared)
        package_ids = {package.resolved.package.id for package in prepared.packages}
        translation_packages = [
            package
            for package in prepared.packages
            if package.resolved.package.install.get("mode") == XUNITY_TRANSLATION_MODE
        ]
        if len(translation_packages) > 1:
            raise InstallError("only one XUnity translation package can be installed at a time")
        replaces_translation_root = bool(translation_packages)
        root_id = prepared.resolution.root_id
        warnings: list[str] = []

        if replaces_translation_root:
            previous_translation_ids = {
                package_id
                for package_id, info in next_state["packages"].items()
                if info.get("install_mode") == XUNITY_TRANSLATION_MODE
            }
            reverse = self._reverse_dependencies(next_state)
            for package_id in previous_translation_ids - package_ids:
                required_by = set(reverse.get(package_id, ())) - previous_translation_ids
                if required_by:
                    raise InstallError(
                        f"installed translation package {package_id} is required by: "
                        + ", ".join(sorted(required_by))
                    )
                next_state["packages"].pop(package_id, None)
            for relative, entry in list(next_state["files"].items()):
                if not _is_xunity_translation_path(relative):
                    continue
                outside_owners = set(entry.get("owners", ())) - previous_translation_ids
                if outside_owners:
                    raise InstallError(
                        f"{relative} is managed by a non-translation package: "
                        + ", ".join(sorted(outside_owners))
                    )
                del next_state["files"][relative]

        for package_id in package_ids:
            old_package = next_state["packages"].get(package_id, {})
            for relative in old_package.get("files", []):
                state_key = self._state_file_key(next_state, relative)
                entry = next_state["files"].get(state_key) if state_key else None
                if not entry:
                    continue
                entry["owners"] = [owner for owner in entry.get("owners", []) if owner != package_id]

        for files in incoming.values():
            sample = files[0]
            relative = sample.target
            state_key = self._state_file_key(next_state, relative)
            existing_entry = next_state["files"].get(state_key) if state_key else None
            target = _safe_game_path(game_dir, relative)
            if existing_entry:
                outside_owners = set(existing_entry.get("owners", ())) - package_ids
                if outside_owners and existing_entry.get("sha256") != sample.sha256:
                    raise InstallError(
                        f"{relative} is shared with {', '.join(sorted(outside_owners))} and cannot be replaced"
                    )
                if target.is_file() and sha256_file(target) != existing_entry.get("sha256"):
                    raise InstallError(f"managed file was modified outside the manager: {relative}")
                if existing_entry.get("preexisting") and existing_entry.get("sha256") != sample.sha256:
                    raise InstallError(f"preexisting file cannot be replaced automatically: {relative}")
                existing_entry["sha256"] = sample.sha256
                existing_entry["owners"] = sorted(
                    set(existing_entry.get("owners", ())) | {item.package_id for item in files}
                )
                if state_key != relative:
                    del next_state["files"][state_key]
                    next_state["files"][relative] = existing_entry
            else:
                preexisting = target.is_file() and not (
                    replaces_translation_root and _is_xunity_translation_path(relative)
                )
                if preexisting and sha256_file(target) != sample.sha256:
                    raise InstallError(f"unmanaged file already exists at {relative}")
                if preexisting:
                    warnings.append(
                        f"registered preexisting file without taking delete/replace ownership: {relative}"
                    )
                next_state["files"][relative] = {
                    "sha256": sample.sha256,
                    "owners": sorted({item.package_id for item in files}),
                    "preexisting": preexisting,
                }

        obsolete: list[tuple[str, dict[str, Any]]] = []
        for relative, entry in list(next_state["files"].items()):
            if entry.get("owners"):
                continue
            obsolete.append((relative, entry))
            del next_state["files"][relative]

        for package in prepared.packages:
            resolved = package.resolved
            old = state["packages"].get(resolved.package.id, {})
            next_state["packages"][resolved.package.id] = {
                "id": resolved.package.id,
                "name": resolved.package.name,
                "repository": resolved.package.repository,
                "version": str(resolved.release.version),
                "tag": resolved.release.tag,
                "release_id": resolved.release.id,
                "requested": bool(old.get("requested")) or resolved.package.id == root_id,
                "install_mode": resolved.package.install.get("mode", "standard"),
                "dependencies": list(resolved.dependency_ids),
                "files": sorted({file.target for file in package.files}),
                "assets": [
                    {
                        "id": asset.asset.id,
                        "name": asset.asset.name,
                        "sha256": asset.sha256,
                        "publisher_verified": asset.publisher_verified,
                    }
                    for asset in package.assets
                ],
                "installed_at": _utc_now(),
            }

        for orphan_id in self._orphan_packages(next_state):
            orphan = next_state["packages"].pop(orphan_id)
            for relative in orphan.get("files", ()):
                state_key = self._state_file_key(next_state, relative)
                entry = next_state["files"].get(state_key) if state_key else None
                if entry:
                    entry["owners"] = [owner for owner in entry.get("owners", ()) if owner != orphan_id]
        for relative, entry in list(next_state["files"].items()):
            if entry.get("owners"):
                continue
            if not any(item[0].casefold() == relative.casefold() for item in obsolete):
                obsolete.append((relative, entry))
            del next_state["files"][relative]

        transaction = self._create_transaction_dir()
        backups: dict[Path, Path | None] = {}
        directory_backups: dict[Path, Path | None] = {}
        try:
            if replaces_translation_root:
                translation_root = _safe_game_path(game_dir, "AutoTranslator")
                archive = self._archive_xunity_translation_backup(translation_root)
                if archive is not None and progress:
                    progress(f"Backed up AutoTranslator: {archive.name}")
                self._backup_directory(translation_root, game_dir, transaction, directory_backups)
                if translation_root.exists():
                    shutil.rmtree(translation_root)
                if progress:
                    progress("Cleared AutoTranslator")

            for relative, entry in obsolete:
                target = _safe_game_path(game_dir, relative)
                if entry.get("preexisting"):
                    continue
                if not target.is_file():
                    continue
                if sha256_file(target) != entry.get("sha256"):
                    warnings.append(f"preserved modified obsolete file: {relative}")
                    continue
                self._backup(target, game_dir, transaction, backups)
                target.unlink()

            for files in incoming.values():
                sample = files[0]
                target = _safe_game_path(game_dir, sample.target)
                if target.is_file() and sha256_file(target) == sample.sha256:
                    continue
                self._backup(target, game_dir, transaction, backups)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.smm-{uuid.uuid4().hex}.tmp")
                shutil.copy2(sample.source, temporary)
                os.replace(temporary, target)
                if progress:
                    progress(f"Installed {sample.target}")

            self.state_store.save(next_state)
        except Exception:
            self._rollback(backups)
            self._rollback_directories(directory_backups)
            raise
        finally:
            shutil.rmtree(transaction, ignore_errors=True)
        return warnings

    def adopt(
        self,
        package: RegistryPackage,
        release: ReleaseInfo,
        files: tuple[PreparedFile, ...],
        assets: tuple[ReleaseAsset, ...],
        dependencies: tuple[str, ...],
        game_dir: Path,
    ) -> bool:
        game_dir = self.validate_game_dir(game_dir)
        state = self.state_store.load()
        if package.id in state["packages"]:
            return False
        if not files:
            raise InstallError(f"cannot adopt {package.id} without matched files")

        next_state = copy.deepcopy(state)
        for file in files:
            target = _safe_game_path(game_dir, file.target)
            if not target.is_file() or sha256_file(target) != file.sha256:
                raise InstallError(f"adoption candidate changed during scan: {file.target}")
            state_key = self._state_file_key(next_state, file.target)
            if state_key:
                entry = next_state["files"][state_key]
                if entry.get("owners") or entry.get("sha256") != file.sha256:
                    raise InstallError(f"file is already managed or conflicts: {file.target}")
                if state_key != file.target:
                    del next_state["files"][state_key]
            next_state["files"][file.target] = {
                "sha256": file.sha256,
                "owners": [package.id],
                "preexisting": False,
                "adopted": True,
            }

        next_state["packages"][package.id] = {
            "id": package.id,
            "name": package.name,
            "repository": package.repository,
            "version": str(release.version),
            "tag": release.tag,
            "release_id": release.id,
            "requested": True,
            "adopted": True,
            "dependencies": list(dependencies),
            "files": sorted(file.target for file in files),
            "assets": [
                {
                    "id": asset.id,
                    "name": asset.name,
                    "sha256": release_asset_sha256(asset),
                    "publisher_verified": True,
                }
                for asset in assets
            ],
            "installed_at": _utc_now(),
        }
        self.state_store.save(next_state)
        return True

    def remove(self, package_id: str, game_dir: Path) -> tuple[list[str], list[str]]:
        game_dir = self.validate_game_dir(game_dir)
        if sprocket_is_running():
            raise InstallError("Sprocket is running; close the game before changing mod files")
        state = self.state_store.load()
        if package_id not in state["packages"]:
            raise InstallError(f"package is not installed: {package_id}")
        reverse = self._reverse_dependencies(state)
        required_by = sorted(reverse.get(package_id, set()))
        if required_by:
            raise InstallError(f"{package_id} is required by: {', '.join(required_by)}")

        removing = {package_id}
        changed = True
        while changed:
            changed = False
            remaining = set(state["packages"]) - removing
            still_required = {
                dependency
                for owner in remaining
                for dependency in state["packages"][owner].get("dependencies", ())
            }
            for current in list(removing):
                for dependency in state["packages"][current].get("dependencies", ()):
                    info = state["packages"].get(dependency)
                    if info and not info.get("requested") and dependency not in still_required and dependency not in removing:
                        removing.add(dependency)
                        changed = True

        next_state = copy.deepcopy(state)
        for current in removing:
            package = next_state["packages"].pop(current)
            for relative in package.get("files", ()):
                entry = next_state["files"].get(relative)
                if entry:
                    entry["owners"] = [owner for owner in entry.get("owners", ()) if owner != current]

        warnings: list[str] = []
        transaction = self._create_transaction_dir()
        backups: dict[Path, Path | None] = {}
        try:
            for relative, entry in list(next_state["files"].items()):
                if entry.get("owners"):
                    continue
                del next_state["files"][relative]
                if entry.get("preexisting"):
                    continue
                target = _safe_game_path(game_dir, relative)
                if not target.is_file():
                    continue
                if sha256_file(target) != entry.get("sha256"):
                    warnings.append(f"preserved modified file: {relative}")
                    continue
                self._backup(target, game_dir, transaction, backups)
                target.unlink()
            self.state_store.save(next_state)
        except Exception:
            self._rollback(backups)
            raise
        finally:
            shutil.rmtree(transaction, ignore_errors=True)
        self._remove_empty_managed_directories(game_dir)
        return sorted(removing), warnings

    @staticmethod
    def _reverse_dependencies(state: dict[str, Any]) -> dict[str, set[str]]:
        reverse: dict[str, set[str]] = {}
        for package_id, info in state["packages"].items():
            for dependency in info.get("dependencies", ()):
                reverse.setdefault(dependency, set()).add(package_id)
        return reverse

    @staticmethod
    def _orphan_packages(state: dict[str, Any]) -> set[str]:
        remaining = set(state["packages"])
        orphans: set[str] = set()
        while True:
            required = {
                dependency
                for package_id in remaining
                for dependency in state["packages"][package_id].get("dependencies", ())
                if dependency in remaining
            }
            found = {
                package_id
                for package_id in remaining
                if not state["packages"][package_id].get("requested") and package_id not in required
            }
            if not found:
                return orphans
            remaining -= found
            orphans |= found

    @staticmethod
    def _state_file_key(state: dict[str, Any], relative: str) -> str | None:
        folded = relative.casefold()
        return next((key for key in state["files"] if key.casefold() == folded), None)

    def _create_transaction_dir(self) -> Path:
        root = self.app_dir / "transactions"
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="txn-", dir=root))

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
        relative = target.resolve().relative_to(game_dir.resolve())
        backup = transaction / "backup" / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        backups[target] = backup

    @staticmethod
    def _backup_directory(
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
        if not target.is_dir():
            raise InstallError(f"translation target is not a directory: {target}")
        relative = target.resolve().relative_to(game_dir.resolve())
        backup = transaction / "directory-backup" / relative
        shutil.copytree(target, backup)
        backups[target] = backup

    def _archive_xunity_translation_backup(self, target: Path) -> Path | None:
        if not target.exists():
            return None
        if not target.is_dir():
            raise InstallError(f"translation target is not a directory: {target}")

        backup_dir = self.app_dir / "backups" / "AutoTranslator"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        archive_path = backup_dir / f"AutoTranslator-{timestamp}.zip"
        temporary = backup_dir / f".{archive_path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with zipfile.ZipFile(
                temporary,
                "x",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                for current, directories, files in os.walk(target, topdown=True, followlinks=False):
                    current_path = Path(current)
                    directories.sort(key=str.casefold)
                    files.sort(key=str.casefold)
                    for name in directories:
                        self._reject_linked_backup_path(current_path / name)
                    for name in files:
                        path = current_path / name
                        self._reject_linked_backup_path(path)
                        if not path.is_file():
                            raise InstallError(f"cannot back up unsupported path: {path}")
                        archive.write(path, path.relative_to(target).as_posix())
                    if current_path != target and not directories and not files:
                        relative = current_path.relative_to(target).as_posix()
                        archive.writestr(relative.rstrip("/") + "/", b"")
            os.replace(temporary, archive_path)
            archives = sorted(backup_dir.glob("AutoTranslator-*.zip"), key=lambda item: item.name)
            for obsolete in archives[:-XUNITY_TRANSLATION_BACKUP_LIMIT]:
                obsolete.unlink()
            return archive_path
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if archive_path.exists():
                archive_path.unlink(missing_ok=True)
            if isinstance(exc, InstallError):
                raise
            raise InstallError(f"cannot archive AutoTranslator backup: {exc}") from exc

    @staticmethod
    def _reject_linked_backup_path(path: Path) -> None:
        is_junction = getattr(path, "is_junction", lambda: False)
        if path.is_symlink() or is_junction():
            raise InstallError(f"cannot back up linked path: {path}")

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

    @staticmethod
    def _rollback_directories(backups: dict[Path, Path | None]) -> None:
        for target, backup in reversed(list(backups.items())):
            try:
                if target.exists():
                    shutil.rmtree(target)
                if backup is not None:
                    shutil.copytree(backup, target)
            except OSError:
                pass

    @staticmethod
    def _remove_empty_managed_directories(game_dir: Path) -> None:
        for root_name in ("Mods", "Plugins", "UserLibs", "UserData"):
            root = game_dir / root_name
            if not root.is_dir():
                continue
            for current, directories, files in os.walk(root, topdown=False):
                path = Path(current)
                if path == root:
                    continue
                if not directories and not files:
                    try:
                        path.rmdir()
                    except OSError:
                        pass
