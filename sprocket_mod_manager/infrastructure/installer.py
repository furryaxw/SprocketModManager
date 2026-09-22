from __future__ import annotations

import copy
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_transaction import FileTransaction
from .manager_paths import backups_dir, manager_state_dir
from .mod_toggle import actual_path_for, canonical_relative, is_disabled_path
from .release_checksums import release_asset_sha256
from .state import StateStore
from .xunity_backup import archive_xunity_translation_backup, is_xunity_translation_path
from ..domain.errors import InstallConflictError, InstallError
from ..domain.models import (
    PreparedFile,
    PreparedPlan,
    ProgressCallback,
    RegistryPackage,
    ReleaseAsset,
    ReleaseInfo,
)
from ..utilities.checksums import sha256_file
from ..utilities.package_paths import XUNITY_TRANSLATION_MODE, validate_target
from ..utilities.processes import sprocket_is_running

LOGGER = logging.getLogger(__name__)


def _safe_game_path(game_dir: Path, relative: str) -> Path:
    target = validate_target(relative)
    root = game_dir.resolve()
    full = (root / Path(*target.parts)).resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise InstallError(f"install target escapes the game directory: {relative}") from exc
    return full


def _existing_managed_file(game_dir: Path, canonical: str) -> Path | None:
    """该逻辑文件在磁盘上的真实路径（先做越界校验，两种变体交给 `mod_toggle.actual_path_for`）。"""
    return actual_path_for(_safe_game_path(game_dir, canonical))


def _missing_parents(game_dir: Path, directory: Path) -> list[str]:
    """这次会被新建的目录（相对游戏目录，自浅到深）。用于记录"安装时创建了哪些文件夹"。"""
    try:
        root = game_dir.resolve()
        current = directory.resolve()
    except OSError:
        return []
    missing: list[str] = []
    while current != root and root in current.parents and not current.is_dir():
        missing.append(current.relative_to(root).as_posix())
        current = current.parent
    return list(reversed(missing))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
            force_conflicts: bool = False,
    ) -> list[str]:
        game_dir = self.validate_game_dir(game_dir)
        LOGGER.info(
            "applying install plan root=%s packages=%d game_dir=%s force_conflicts=%s",
            prepared.resolution.root_id,
            len(prepared.packages),
            game_dir,
            force_conflicts,
        )
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
                if not is_xunity_translation_path(relative):
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
                if target.is_file() and sha256_file(target) != existing_entry.get("sha256") and not force_conflicts:
                    raise InstallConflictError(f"managed file was modified outside the manager: {relative}")
                existing_entry["sha256"] = sample.sha256
                existing_entry["owners"] = sorted(
                    set(existing_entry.get("owners", ())) | {item.package_id for item in files}
                )
                if state_key != relative:
                    del next_state["files"][state_key]
                    next_state["files"][relative] = existing_entry
            else:
                # 目标已经存在、但不在记录里：内容不同就是冲突（除非强制覆盖）；内容相同则直接登记为受管文件。
                unmanaged_existing = target.is_file() and not (
                        replaces_translation_root and is_xunity_translation_path(relative)
                )
                if unmanaged_existing and sha256_file(target) != sample.sha256 and not force_conflicts:
                    raise InstallConflictError(f"unmanaged file already exists at {relative}")
                next_state["files"][relative] = {
                    "sha256": sample.sha256,
                    "owners": sorted({item.package_id for item in files}),
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

        transaction = FileTransaction(manager_state_dir(game_dir))
        try:
            if replaces_translation_root:
                translation_root = _safe_game_path(game_dir, "AutoTranslator")
                archive = archive_xunity_translation_backup(backups_dir(game_dir), translation_root)
                if archive is not None and progress:
                    progress(f"Backed up AutoTranslator: {archive.name}")
                transaction.backup_directory(translation_root, game_dir)
                if translation_root.exists():
                    shutil.rmtree(translation_root)
                if progress:
                    progress("Cleared AutoTranslator")

            for relative, entry in obsolete:
                target = _existing_managed_file(game_dir, relative)
                if target is None:
                    continue
                if sha256_file(target) != entry.get("sha256"):
                    warnings.append(f"preserved modified obsolete file: {relative}")
                    continue
                transaction.backup_file(target, game_dir)
                target.unlink()

            created_by_package: dict[str, set[str]] = {}
            for files in incoming.values():
                sample = files[0]
                target = _safe_game_path(game_dir, sample.target)
                if target.is_file() and sha256_file(target) == sample.sha256:
                    continue
                transaction.backup_file(target, game_dir)
                for relative in _missing_parents(game_dir, target.parent):
                    if "/" not in relative:
                        continue  # `Mods` / `Plugins` / `UserLibs` 这类游戏根目录不记、也不删
                    for item in files:
                        created_by_package.setdefault(item.package_id, set()).add(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.smm-{uuid.uuid4().hex}.tmp")
                shutil.copy2(sample.source, temporary)
                os.replace(temporary, target)
                if progress:
                    progress(f"Installed {sample.target}")

            for package_id, directories in created_by_package.items():
                record = next_state["packages"].get(package_id)
                if isinstance(record, dict) and directories:
                    record["directories"] = sorted(
                        set(record.get("directories", ())) | directories
                    )
            self.state_store.save(next_state)
        except Exception:
            LOGGER.exception("install transaction failed; rolling back root=%s", root_id)
            transaction.rollback()
            raise
        finally:
            transaction.close()
        LOGGER.info("install plan applied root=%s warnings=%d", root_id, len(warnings))
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
        LOGGER.info("adopting existing package=%s game_dir=%s", package.id, game_dir)
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
            }

        next_state["packages"][package.id] = {
            "id": package.id,
            "name": package.name,
            "repository": package.repository,
            "version": str(release.version),
            "tag": release.tag,
            "release_id": release.id,
            "requested": True,
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
        }
        self.state_store.save(next_state)
        LOGGER.info("existing package adopted package=%s files=%d", package.id, len(files))
        return True

    def remove(self, package_id: str, game_dir: Path) -> tuple[list[str], list[str]]:
        game_dir = self.validate_game_dir(game_dir)
        LOGGER.info("removing package=%s game_dir=%s", package_id, game_dir)
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
                    if info and not info.get(
                            "requested") and dependency not in still_required and dependency not in removing:
                        removing.add(dependency)
                        changed = True

        next_state = copy.deepcopy(state)
        recorded_directories: set[str] = set()
        for current in removing:
            package = next_state["packages"].pop(current)
            recorded_directories |= {
                item for item in package.get("directories", ()) if isinstance(item, str)
            }
            for relative in package.get("files", ()):
                entry = next_state["files"].get(relative)
                if entry:
                    entry["owners"] = [owner for owner in entry.get("owners", ()) if owner != current]

        warnings: list[str] = []
        transaction = FileTransaction(manager_state_dir(game_dir))
        try:
            for relative, entry in list(next_state["files"].items()):
                if entry.get("owners"):
                    continue
                del next_state["files"][relative]
                target = _existing_managed_file(game_dir, relative)
                if target is None:
                    continue
                if sha256_file(target) != entry.get("sha256"):
                    warnings.append(f"preserved modified file: {relative}")
                    continue
                transaction.backup_file(target, game_dir)
                target.unlink()
            self.state_store.save(next_state)
        except Exception:
            LOGGER.exception("remove transaction failed; rolling back package=%s", package_id)
            transaction.rollback()
            raise
        finally:
            transaction.close()
        for relative in sorted(recorded_directories, key=lambda item: item.count("/"), reverse=True):
            candidate = _safe_game_path(game_dir, relative)
            try:
                candidate.rmdir()  # 只有空目录会成功：用户往里放过东西就保留
            except OSError:
                continue
        self._remove_empty_managed_directories(game_dir)
        LOGGER.info("packages removed requested=%s count=%d warnings=%d directories=%d",
                    package_id, len(removing), len(warnings), len(recorded_directories))
        return sorted(removing), warnings

    def reconcile(self, game_dir: Path) -> list[str]:
        """把安装记录对齐到磁盘：文件不在了就删记录，包没有文件了就删包。

        这是「纯扫描」模型的底线保证——**磁盘是唯一事实来源**。用户手工删掉（或改名）的 DLL
        不允许继续以"已安装"的身份留在记录里，否则就会出现本地文件已经没了、管理器还在显示的
        幽灵条目。改名（禁用/启用）走 `rename_managed_file`，所以对不上号的改名会被这里当作
        删除处理：模组退回 "Local only"，与扫描结果一致。

        返回被丢弃的键（文件路径 + 包 id），供测试与诊断使用。
        """
        root = Path(game_dir).expanduser()
        if not root.is_dir():
            return []
        state = self.state_store.load()
        next_state = copy.deepcopy(state)
        dropped: list[str] = []
        for relative in list(next_state["files"]):
            try:
                target = _safe_game_path(root, relative)
            except InstallError:
                LOGGER.warning("dropping recorded path that escapes the game directory: %s", relative)
                del next_state["files"][relative]
                dropped.append(relative)
                continue
            actual = _existing_managed_file(root, relative)
            if actual is None:
                del next_state["files"][relative]
                dropped.append(relative)
                continue
            next_state["files"][relative]["disabled"] = is_disabled_path(actual)
        for package_id, package in list(next_state["packages"].items()):
            recorded = [item for item in package.get("files", ()) if isinstance(item, str)]
            kept = [item for item in recorded if item in next_state["files"]]
            if len(kept) != len(recorded):
                package["files"] = kept
            if not kept:
                del next_state["packages"][package_id]
                dropped.append(package_id)
        if dropped:
            self.state_store.save(next_state)
            LOGGER.info("install state reconciled with disk dropped=%s", dropped)
        return dropped

    def verify(self, game_dir: Path) -> dict[str, Any]:
        """强制重算已安装文件的 SHA-256 并报告差异（**不写状态文件**）。

        「是否还是某个发布版本」不在这一层判断：那需要 Registry 的发布历史，由
        `ModManagerService.verify_installed` 结合 `application/integrity.py` 得出。
        这里只回答两个事实：文件还在不在、内容与安装记录是否一致。
        """
        root = Path(game_dir).expanduser()
        if not root.is_dir():
            return {"checked": 0, "changed": [], "missing": [], "hashes": {}}

        state = self.state_store.load()
        changed: list[str] = []
        missing: list[str] = []
        hashes: dict[str, str] = {}
        for relative, entry in state["files"].items():
            actual = _existing_managed_file(root, relative)
            if actual is None:
                missing.append(relative)
                continue
            try:
                digest = sha256_file(actual)
            except OSError as exc:
                LOGGER.warning("could not hash managed file %s: %s", relative, exc)
                missing.append(relative)
                continue
            hashes[relative] = digest
            expected = str(entry.get("sha256") or "")
            if expected and digest != expected:
                changed.append(relative)

        LOGGER.info("install state verified checked=%d changed=%d missing=%d",
                    len(state["files"]), len(changed), len(missing))
        return {
            "checked": len(state["files"]),
            "changed": sorted(changed),
            "missing": sorted(missing),
            "hashes": hashes,
        }

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
