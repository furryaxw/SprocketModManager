from __future__ import annotations

import copy
import fnmatch
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .file_transaction import FileTransaction
from .manager_paths import backups_dir, manager_state_dir
from .mod_toggle import actual_path_for, canonical_relative, is_disabled_path
from .release_checksums import release_asset_sha256
from .state import StateStore
from .xunity_backup import archive_replaced_directory, restore_replaced_directory
from ..domain.errors import InstallConflictError, InstallError, ScanError
from ..domain.models import (
    MODLOADER_KIND,
    PreparedFile,
    PreparedPackage,
    PreparedPlan,
    ProgressCallback,
    RegistryPackage,
    ReleaseAsset,
    ReleaseInfo,
)
from ..utilities.checksums import sha256_file
from ..utilities.package_paths import (
    validate_relative_path,
    validate_subpath,
    validate_supply_target,
)
from ..utilities.processes import sprocket_is_running

LOGGER = logging.getLogger(__name__)

# 卸载后顺手清空的目录根：基础运行时自己声明的目录另由 `_remove_loader_directories` 整树交还。
DEFAULT_MANAGED_ROOTS = ("Mods", "Plugins", "UserLibs", "UserData")

# 补丁模式：安装时替换别的包的文件，卸载时还原。归档与事务无关，事务提交后仍保留。
PATCH_MODE = "patch"
PATCHED_BACKUP_DIR_NAME = "patched"


def _is_under_any(relative: str, directories: Iterable[str]) -> bool:
    """这个路径是否落在某个「整体接管」的目录里（含目录本身）。"""
    folded = canonical_relative(relative).casefold()
    for directory in directories:
        prefix = canonical_relative(str(directory)).strip("/").casefold()
        if not prefix or prefix == ".":
            return True
        if folded == prefix or folded.startswith(prefix + "/"):
            return True
    return False


def _is_modloader_record(package: dict[str, Any]) -> bool:
    """安装记录是不是基础运行时：只有它不记逐文件清单。"""
    return str(package.get("kind") or "") == MODLOADER_KIND


def _payload_rule_matches(rule: dict[str, str], source_name: str) -> bool:
    pattern = str(rule.get("match", ""))
    folded = source_name.casefold()
    return fnmatch.fnmatchcase(folded, pattern.casefold()) or fnmatch.fnmatchcase(
        PurePosixPath(source_name).name.casefold(), pattern.casefold()
    )


def _recorded_directories(package: dict[str, Any]) -> set[str]:
    return {
        item
        for item in package.get("directories", ())
        if isinstance(item, str) and canonical_relative(item).strip("/")
    }


def _recorded_payload_files(package: dict[str, Any]) -> list[dict[str, str]]:
    entries = package.get("payload_files")
    if not isinstance(entries, list):
        return []
    recorded: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if isinstance(path, str) and path.strip():
            recorded.append(
                {"path": canonical_relative(path), "sha256": str(item.get("sha256") or "")}
            )
    return recorded


def _path_exists(game_dir: Path, relative: str) -> bool:
    try:
        return _existing_managed_file(game_dir, relative) is not None
    except InstallError:
        return False


def _directory_exists(game_dir: Path, relative: str) -> bool:
    try:
        return _safe_game_path(game_dir, relative).is_dir()
    except InstallError:
        return False


def _directory_has_other_owners(state: dict[str, Any], relative: str, removing: Iterable[str]) -> bool:
    """这个目录（含子目录）里有没有别的包仍然拥有的文件。"""
    gone = {str(item) for item in removing}
    for path, entry in (state.get("files") or {}).items():
        if not isinstance(entry, dict) or not _is_under_any(str(path), [relative]):
            continue
        if {str(owner) for owner in entry.get("owners", ())} - gone:
            return True
    return False


def _safe_game_path(game_dir: Path, relative: str) -> Path:
    # 目标根由注册表的供给表决定（`Mods`、`BepInEx/plugins`、加载器自己的游戏根载荷），
    # 所以这里只保证是安全的相对路径并且不越出游戏目录。
    target = validate_relative_path(relative)
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


def _patched_backup_dir(game_dir: Path) -> Path:
    """补丁覆盖的原件归档根：`<game>/SprocketModManager/backup/patched`。"""
    return backups_dir(game_dir) / PATCHED_BACKUP_DIR_NAME


def _patched_archive_path(game_dir: Path, relative: str) -> Path:
    """某个逻辑文件的原件归档位置：归档按规范相对路径平铺。

    归档本身（位置 + 内容）就是这份替换记录：一个路径在这里意味着它被补丁替换过，原件的摘要
    也由归档内容现算，所以不往安装记录里再抄一份。键取规范路径，启用/禁用改名后仍然找得到同一
    份归档。
    """
    return _patched_backup_dir(game_dir) / Path(*validate_relative_path(canonical_relative(relative)).parts)


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
        # 上一次 `apply` 装了多少个目标文件：调用方（加载器页的提示）据此报出真实写入数。
        self.last_applied_files = 0

    @staticmethod
    def validate_game_dir(game_dir: Path) -> Path:
        resolved = game_dir.expanduser().resolve()
        if not resolved.is_dir() or not (resolved / "Sprocket.exe").is_file():
            raise InstallError(f"not a Sprocket game directory: {resolved}")
        return resolved

    @staticmethod
    def _incoming_files(prepared: PreparedPlan) -> dict[str, list[PreparedFile]]:
        """把所有包的文件按目标路径归组：补丁与它替换的包会指向同一个目标。"""
        incoming: dict[str, list[PreparedFile]] = {}
        for package in prepared.packages:
            for file in package.files:
                incoming.setdefault(file.target.casefold(), []).append(file)
        return incoming

    @staticmethod
    def _resolved_incoming(
            files: list[PreparedFile],
            patch_package_ids: set[str],
    ) -> tuple[PreparedFile, bool]:
        """同一个目标上的文件取哪一份：补丁的内容覆盖别的包。

        非补丁文件之间内容不一致仍然是计划自身冲突；补丁之间也一样（两个补丁改同一个文件没有
        确定的先后）。返回 `(落盘的文件, 是否来自补丁包)`。
        """
        patch_files = [file for file in files if file.package_id in patch_package_ids]
        other_files = [file for file in files if file.package_id not in patch_package_ids]
        for group in (patch_files, other_files):
            if len({file.sha256 for file in group}) > 1:
                raise InstallError(f"install plan has a file conflict at {group[0].target}")
        chosen = patch_files or other_files
        return chosen[0], bool(patch_files)

    @staticmethod
    def _replaced_record(game_dir: Path, relative: str) -> dict[str, str] | None:
        """这个文件被补丁替换前的原件：摘要 + 游戏目录内的相对归档路径。

        记录的唯一持久来源是归档：它不在，就是没有替换过（不猜"还原成什么"）。
        """
        archive = _patched_archive_path(game_dir, relative)
        if not archive.is_file():
            return None
        return {
            "sha256": sha256_file(archive),
            "backup": archive.relative_to(Path(game_dir).resolve()).as_posix(),
        }

    @staticmethod
    def _archive_patched_file(game_dir: Path, relative: str, source: Path) -> str:
        """把被替换的内容复制进归档区，返回游戏目录内的相对归档路径。"""
        archive = _patched_archive_path(game_dir, relative)
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, archive)
        return archive.relative_to(Path(game_dir).resolve()).as_posix()

    @staticmethod
    def _prune_patched_archives(game_dir: Path, state: dict[str, Any]) -> None:
        """删掉没有被任何补丁包持有的归档条目（补丁整个卸载后它们就没有用了）。"""
        root = _patched_backup_dir(game_dir)
        if not root.is_dir():
            return
        patch_owners = {
            package_id
            for package_id, info in state["packages"].items()
            if info.get("install_mode") == PATCH_MODE
        }
        referenced = {
            relative.casefold()
            for relative, entry in state["files"].items()
            if set(entry.get("owners", ())) & patch_owners
        }
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.relative_to(root).as_posix().casefold() in referenced:
                continue
            try:
                path.unlink()
            except OSError:
                LOGGER.warning("could not delete unused patch archive: %s", path)
        for current, directories, files in os.walk(root, topdown=False):
            if Path(current) == root or directories or files:
                continue
            try:
                Path(current).rmdir()
            except OSError:
                pass

    @staticmethod
    def _payload_entries(package: PreparedPackage) -> tuple[list[str], list[dict[str, str]]]:
        """基础运行时载荷落地的**顶层条目**：目录清单 + 游戏根目录里的零散文件。

        目标就是游戏根目录（`{Sprocket}`）时，落点由压缩包里的顶层条目决定 —— 顶层目录是
        加载器自己的树，顶层文件（`version.dll`、`winhttp.dll` 之类）是让游戏加载加载器的
        代理，两者卸载时都要交还。规则目标更深时直接记那个目录。没有 `payload` 规则的加载器
        按类型安装，落点就是各文件所在的目录/文件。

        顶层文件连同它安装时的摘要一起记：卸载只删内容没变过的那份，用户改过就留下并警告。
        """
        directories: set[str] = set()
        files: dict[str, tuple[str, str]] = {}
        for rule in package.resolved.package.payload_rules:
            try:
                target = validate_supply_target(str(rule.get("target", "")))
                if rule.get("subpath"):
                    target = target / validate_subpath(str(rule["subpath"]))
            except ScanError:
                continue
            parts = [part for part in target.parts if part != "."]
            if parts:
                directories.add("/".join(parts))
                continue
            for file in package.files:
                if not _payload_rule_matches(rule, file.source_name):
                    continue
                file_parts = PurePosixPath(file.target).parts
                if len(file_parts) > 1:
                    directories.add(file_parts[0])
                else:
                    files.setdefault(file_parts[0].casefold(), (file_parts[0], file.sha256))
        if not directories and not files:
            for file in package.files:
                file_parts = PurePosixPath(file.target).parts
                if len(file_parts) > 1:
                    directories.add("/".join(file_parts[:-1]))
                else:
                    files.setdefault(file_parts[0].casefold(), (file_parts[0], file.sha256))
        return (
            sorted(directories),
            [
                {"path": files[key][0], "sha256": files[key][1]}
                for key in sorted(files)
            ],
        )

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
        if sprocket_is_running(game_dir):
            raise InstallError("Sprocket is running; close the game before changing mod files")
        state = self.state_store.load()
        next_state = copy.deepcopy(state)
        incoming = self._incoming_files(prepared)
        package_ids = {package.resolved.package.id for package in prepared.packages}
        # 基础运行时不记逐文件清单：它的记录只有版本、资产与安装时落地的目录。
        modloader_ids = {
            package.resolved.package.id
            for package in prepared.packages
            if package.resolved.package.kind == MODLOADER_KIND
        }
        patch_package_ids = {
            package.resolved.package.id
            for package in prepared.packages
            if package.resolved.package.install.get("mode") == PATCH_MODE
        }
        # 同一目标上的内容先定下来（补丁赢），后面记录与落盘都按同一份来。
        plan_files = [
            (files, *self._resolved_incoming(files, patch_package_ids))
            for files in incoming.values()
        ]
        # 这次计划落盘的文件数（每个目标一份）：记录里不再逐文件记账，调用方需要另行拿到它。
        self.last_applied_files = len(plan_files)
        # 「整体接管」的类型：安装时备份并清空该类型的供给目录，卸载时整目录还原。
        replaced_by_type: dict[str, str] = {}
        for package in prepared.packages:
            for file_type in package.resolved.package.replace_types():
                previous = replaced_by_type.get(file_type)
                if previous is not None and previous != package.resolved.package.id:
                    raise InstallError(
                        f"install plan has two packages replacing {file_type}: "
                        f"{previous} and {package.resolved.package.id}"
                    )
                replaced_by_type[file_type] = package.resolved.package.id
        replaced_directories: dict[str, str] = {}
        for file_type in replaced_by_type:
            directory = prepared.install_directories.get(file_type)
            if directory is None:
                raise InstallError(f"cannot resolve the directory replaced by {file_type}")
            replaced_directories[file_type] = directory.as_posix()
        replaced_directory_list = list(replaced_directories.values())
        root_id = prepared.resolution.root_id
        warnings: list[str] = []

        if replaced_directories:
            previous_replacing = {
                package_id
                for package_id, info in next_state["packages"].items()
                if set(info.get("replaced_types", ())) & set(replaced_directories)
            }
            reverse = self._reverse_dependencies(next_state)
            displaced_directories: list[str] = []
            for package_id in previous_replacing - package_ids:
                required_by = set(reverse.get(package_id, ())) - previous_replacing
                if required_by:
                    raise InstallError(
                        f"installed replacing package {package_id} is required by: "
                        + ", ".join(sorted(required_by))
                    )
                displaced_directories.extend(
                    str(item)
                    for item in (
                        next_state["packages"][package_id].get("replaced_directories") or {}
                    ).values()
                )
                next_state["packages"].pop(package_id, None)
            taken_directories = displaced_directories + replaced_directory_list
            for relative, entry in list(next_state["files"].items()):
                if not _is_under_any(relative, taken_directories):
                    continue
                outside_owners = set(entry.get("owners", ())) - previous_replacing
                if outside_owners:
                    raise InstallError(
                        f"{relative} is managed by a non-replacing package: "
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

        for files, sample, is_patch in plan_files:
            relative = sample.target
            recorded_owners = sorted({item.package_id for item in files} - modloader_ids)
            if not recorded_owners:
                # 基础运行时自己的载荷：整棵树归它，不逐文件记账；重新安装覆盖同名文件是正常更新，
                # 所以这里也不做"记录之外的已存在文件"冲突判定。
                continue
            state_key = self._state_file_key(next_state, relative)
            existing_entry = next_state["files"].get(state_key) if state_key else None
            target = _safe_game_path(game_dir, relative)
            if existing_entry:
                outside_owners = set(existing_entry.get("owners", ())) - package_ids
                if outside_owners and existing_entry.get("sha256") != sample.sha256 and not is_patch:
                    raise InstallError(
                        f"{relative} is shared with {', '.join(sorted(outside_owners))} and cannot be replaced"
                    )
                if (
                        target.is_file()
                        and sha256_file(target) != existing_entry.get("sha256")
                        and not is_patch
                        and not force_conflicts
                ):
                    raise InstallConflictError(f"managed file was modified outside the manager: {relative}")
                existing_entry["sha256"] = sample.sha256
                existing_entry["owners"] = sorted(
                    set(existing_entry.get("owners", ())) | set(recorded_owners)
                )
                if state_key != relative:
                    del next_state["files"][state_key]
                    next_state["files"][relative] = existing_entry
            else:
                # 目标已经存在、但不在记录里：内容不同就是冲突（除非强制覆盖）；内容相同则直接登记为受管文件。
                # 补丁本来就是来换掉别人的文件的，所以它的目标不受这条约束。
                unmanaged_existing = target.is_file() and not _is_under_any(
                    relative, replaced_directory_list
                )
                if unmanaged_existing and sha256_file(target) != sample.sha256 and not is_patch and not force_conflicts:
                    raise InstallConflictError(f"unmanaged file already exists at {relative}")
                next_state["files"][relative] = {
                    "sha256": sample.sha256,
                    "owners": recorded_owners,
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
            record: dict[str, Any] = {
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
            if resolved.package.id in modloader_ids:
                # 基础运行时只记"装了哪些顶层条目"：卸载按这份清单交还整棵树与代理文件，
                # 逐文件路径与摘要一概不留。
                payload_directories, payload_files = self._payload_entries(package)
                record["kind"] = MODLOADER_KIND
                record["files"] = []
                record["directories"] = payload_directories
                record["payload_files"] = payload_files
            replaced_types = resolved.package.replace_types()
            if replaced_types:
                record["replaced_types"] = sorted(replaced_types)
                record["replaced_directories"] = {
                    file_type: replaced_directories[file_type]
                    for file_type in replaced_types
                    if file_type in replaced_directories
                }
            next_state["packages"][resolved.package.id] = record

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
            for file_type, directory in replaced_directories.items():
                target = _safe_game_path(game_dir, directory)
                archive = archive_replaced_directory(backups_dir(game_dir), file_type, target)
                if archive is not None and progress:
                    progress(f"Backed up {file_type}: {archive.name}")
                transaction.backup_directory(target, game_dir)
                if target.exists():
                    shutil.rmtree(target)
                if progress:
                    progress(f"Cleared {directory}")

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
            for files, sample, is_patch in plan_files:
                target = _safe_game_path(game_dir, sample.target)
                if target.is_file() and sha256_file(target) == sample.sha256:
                    continue
                if is_patch and self._replaced_record(game_dir, sample.target) is None:
                    # 补丁替换别的包的文件：被替换的内容另存一份（与事务无关），卸载时据此还原。
                    # 目标还不在磁盘上时（补丁和它的加载器在同一个计划里），那一份内容就是计划里
                    # 被补丁盖掉的文件。
                    displaced = target if target.is_file() else next(
                        (
                            item.source
                            for item in files
                            if item.package_id not in patch_package_ids and item.sha256 != sample.sha256
                        ),
                        None,
                    )
                    if displaced is not None:
                        archived = self._archive_patched_file(game_dir, sample.target, displaced)
                        if progress:
                            progress(f"Archived {sample.target} -> {archived}")
                transaction.backup_file(target, game_dir)
                for relative in _missing_parents(game_dir, target.parent):
                    if "/" not in relative:
                        continue  # `Mods` / `Plugins` / `UserLibs` 这类游戏根目录不记、也不删
                    for item in files:
                        if item.package_id in modloader_ids:
                            continue  # 加载器的目录由 `install.payload` 决定，不从落盘路径推
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

    def adopt_loader(
            self,
            package: RegistryPackage,
            game_dir: Path,
            *,
            version: str,
            directories: tuple[str, ...],
            payload_files: tuple[tuple[str, str], ...],
            dependencies: tuple[str, ...] = (),
            tag: str = "",
            release_id: int | None = None,
    ) -> bool:
        """把**磁盘上已在场**的加载器写进安装记录。

        它的载荷不是管理器装的，所以不记资产、也不逐文件比对：记录只有版本、发布出处与这次检测
        到的事实 —— 运行时自己的树（整树交还）和游戏根目录里的代理文件及其**当下**的摘要。
        目录与代理文件都必须确实存在且摘要一致，否则这次认领不成立。
        """
        game_dir = self.validate_game_dir(game_dir)
        LOGGER.info("adopting existing loader package=%s game_dir=%s", package.id, game_dir)
        state = self.state_store.load()
        if package.id in state["packages"]:
            return False
        for relative in directories:
            if not _safe_game_path(game_dir, relative).is_dir():
                raise InstallError(f"adopted loader directory is missing: {relative}")
        recorded_files: list[dict[str, str]] = []
        for relative, digest in payload_files:
            target = _safe_game_path(game_dir, relative)
            if not target.is_file():
                raise InstallError(f"adopted loader file is missing: {relative}")
            if sha256_file(target) != digest:
                raise InstallError(f"adopted loader file changed during scan: {relative}")
            recorded_files.append({"path": relative, "sha256": digest})
        record: dict[str, Any] = {
            "id": package.id,
            "name": package.name,
            "repository": package.repository,
            "version": version,
            "requested": True,
            "kind": MODLOADER_KIND,
            "dependencies": list(dependencies),
            "files": [],
            "directories": sorted(directories),
            "payload_files": recorded_files,
            "assets": [],
        }
        if tag:
            record["tag"] = tag
        if release_id is not None:
            record["release_id"] = release_id
        next_state = copy.deepcopy(state)
        next_state["packages"][package.id] = record
        self.state_store.save(next_state)
        LOGGER.info(
            "existing loader adopted package=%s version=%s directories=%d files=%d",
            package.id,
            version,
            len(directories),
            len(recorded_files),
        )
        return True

    def remove(
            self,
            package_id: str,
            game_dir: Path,
            *,
            modloaders: Iterable[str] = (),
    ) -> tuple[list[str], list[str]]:
        game_dir = self.validate_game_dir(game_dir)
        LOGGER.info("removing package=%s game_dir=%s", package_id, game_dir)
        if sprocket_is_running(game_dir):
            raise InstallError("Sprocket is running; close the game before changing mod files")
        state = self.state_store.load()
        if package_id not in state["packages"]:
            raise InstallError(f"package is not installed: {package_id}")
        modloader_ids = {str(item) for item in modloaders}
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
        loader_directories: set[str] = set()
        loader_files: list[dict[str, str]] = []
        removed_roots: set[str] = set()
        for current in removing:
            package = next_state["packages"].pop(current)
            if _is_modloader_record(package) or current in modloader_ids:
                # 基础运行时不记逐文件归属：卸载按它自己声明的顶层条目交还整棵树与代理文件。
                loader_directories |= _recorded_directories(package)
                loader_files.extend(_recorded_payload_files(package))
            else:
                recorded_directories |= {
                    item for item in package.get("directories", ()) if isinstance(item, str)
                }
            for relative in package.get("files", ()):
                head, separator, _ = str(relative).partition("/")
                if separator:
                    removed_roots.add(head)
                entry = next_state["files"].get(relative)
                if entry:
                    entry["owners"] = [owner for owner in entry.get("owners", ()) if owner != current]

        # 「整体接管」的目录：卸载时整目录从上一次归档还原，目录里的记录都不再成立。
        replaced_restores: dict[str, str] = {}
        for current in removing:
            for file_type, directory in (
                    state["packages"][current].get("replaced_directories") or {}
            ).items():
                replaced_restores.setdefault(str(file_type), str(directory))
        replaced_directory_list = list(replaced_restores.values())

        # 被卸载的补丁替换过的文件：有归档就还原原件，没归档就是没有替换过。
        # 整体接管目录里的文件不在这里还原 —— 整个目录由归档兜底。
        patched_files: dict[str, dict[str, str]] = {}
        for current in removing:
            if state["packages"][current].get("install_mode") != PATCH_MODE:
                continue
            for relative in state["packages"][current].get("files", ()):
                if _is_under_any(relative, replaced_directory_list):
                    continue
                record = self._replaced_record(game_dir, relative)
                if record is not None:
                    patched_files.setdefault(relative, record)

        warnings: list[str] = []
        transaction = FileTransaction(manager_state_dir(game_dir))
        try:
            for file_type, directory in replaced_restores.items():
                target = _safe_game_path(game_dir, directory)
                for relative in list(next_state["files"]):
                    if _is_under_any(relative, [directory]):
                        del next_state["files"][relative]
                transaction.backup_directory(target, game_dir)
                if target.exists():
                    shutil.rmtree(target)
                restore_replaced_directory(backups_dir(game_dir), file_type, target)

            for relative, record in patched_files.items():
                entry = next_state["files"].get(relative)
                if entry is None:
                    continue
                target = _existing_managed_file(game_dir, relative)
                if target is None:
                    continue
                if sha256_file(target) == entry.get("sha256"):
                    # 磁盘还是补丁装进去的内容：把归档的原件放回去。归档在就说明这里原本有别的内容。
                    transaction.backup_file(target, game_dir)
                    temporary = target.with_name(f".{target.name}.smm-{uuid.uuid4().hex}.tmp")
                    shutil.copy2(_safe_game_path(game_dir, record["backup"]), temporary)
                    os.replace(temporary, target)
                    if entry.get("owners"):
                        entry["sha256"] = record["sha256"]
                    else:
                        # 补丁是唯一归属：归档存在说明这个路径在补丁之前就有内容，还原后文件
                        # 继续不受管理。删掉记录，下面那个"没有归属就删文件"的循环才不会把它清掉。
                        del next_state["files"][relative]
                    continue
                if entry.get("owners"):
                    warnings.append(f"preserved modified file: {relative}")

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
        self._prune_patched_archives(game_dir, next_state)
        for relative in sorted(recorded_directories, key=lambda item: item.count("/"), reverse=True):
            candidate = _safe_game_path(game_dir, relative)
            try:
                candidate.rmdir()  # 只有空目录会成功：用户往里放过东西就保留
            except OSError:
                continue
        self._remove_loader_directories(
            game_dir, loader_directories, state=next_state, removing=removing, warnings=warnings
        )
        self._remove_loader_payload_files(
            game_dir, loader_files, state=next_state, removing=removing, warnings=warnings
        )
        self._remove_empty_managed_directories(game_dir, removed_roots)
        LOGGER.info("packages removed requested=%s count=%d warnings=%d directories=%d",
                    package_id, len(removing), len(warnings), len(recorded_directories))
        return sorted(removing), warnings

    def reconcile(self, game_dir: Path, *, modloaders: Iterable[str] = ()) -> list[str]:
        """把安装记录对齐到磁盘：文件不在了就删记录，包没有文件了就删包。

        这是「纯扫描」模型的底线保证——**磁盘是唯一事实来源**。用户手工删掉（或改名）的 DLL
        不允许继续以"已安装"的身份留在记录里，否则就会出现本地文件已经没了、管理器还在显示的
        幽灵条目。改名（禁用/启用）走 `rename_managed_file`，所以对不上号的改名会被这里当作
        删除处理：模组退回 "Local only"，与扫描结果一致。

        基础运行时没有逐文件记录，在场与否按它声明的目录判：目录一个都不在了才算它被移除。
        `modloaders` 给出调用方已知的加载器包 id，用于把不带 `kind` 的既有记录一并归一。

        返回被丢弃的键（文件路径 + 包 id），供测试与诊断使用。
        """
        root = Path(game_dir).expanduser()
        if not root.is_dir():
            return []
        modloader_ids = {str(item) for item in modloaders}
        before = self.state_store.load() if modloader_ids else None
        next_state = self.state_store.load(modloaders=modloader_ids)
        normalised = before is not None and next_state != before
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
            if kept:
                continue
            if _is_modloader_record(package):
                declared = _recorded_directories(package)
                declared_files = [item["path"] for item in _recorded_payload_files(package)]
                if declared or declared_files:
                    still_there = any(_directory_exists(root, item) for item in declared) or any(
                        _path_exists(root, item) for item in declared_files
                    )
                    if not still_there:
                        del next_state["packages"][package_id]
                        dropped.append(package_id)
                continue
            del next_state["packages"][package_id]
            dropped.append(package_id)
        if dropped or normalised:
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
    def _remove_loader_directories(
            game_dir: Path,
            directories: Iterable[str],
            *,
            state: dict[str, Any],
            removing: Iterable[str],
            warnings: list[str],
    ) -> None:
        """交还基础运行时的目录：整棵树删掉，除非它属于共享根目录或含有别的包拥有的文件。

        这些目录是加载器自己的树（`install.payload` 的落点），树里的内容按约定归它。判不准的
        情形 —— 共享根目录（`Mods` 等，用户和其它模组都在用）、别的包仍有文件在里面 —— 一律
        保留并给出警告，绝不猜着删。
        """
        root = Path(game_dir).resolve()
        shared = {name.casefold() for name in DEFAULT_MANAGED_ROOTS}
        for relative in sorted(directories, key=lambda item: item.count("/"), reverse=True):
            folded = canonical_relative(str(relative)).strip("/")
            if not folded or folded == ".":
                continue
            top = folded.split("/", 1)[0].casefold()
            if top in shared:
                warnings.append(f"preserved shared directory: {relative}")
                continue
            if _directory_has_other_owners(state, relative, removing):
                warnings.append(f"preserved directory owned by other packages: {relative}")
                continue
            try:
                candidate = _safe_game_path(game_dir, relative)
            except InstallError:
                continue
            if candidate == root or not candidate.is_dir():
                continue
            try:
                shutil.rmtree(candidate)
            except OSError:
                warnings.append(f"could not delete loader directory: {relative}")

    @staticmethod
    def _remove_loader_payload_files(
            game_dir: Path,
            entries: Iterable[dict[str, str]],
            *,
            state: dict[str, Any],
            removing: Iterable[str],
            warnings: list[str],
    ) -> None:
        """交还基础运行时落在游戏根目录的顶层文件（代理 DLL 之类）。

        只删安装时记下、且内容与摘要仍然一致的那份。判不准的情形 —— 别的包现在还拥有它、摘要
        对不上、或记录里根本没摘要 —— 一律保留并警告。
        """
        gone = {str(item) for item in removing}
        for entry in entries:
            relative = str(entry.get("path") or "")
            if not relative:
                continue
            folded = relative.casefold()
            state_key = next(
                (key for key in (state.get("files") or {}) if key.casefold() == folded), None
            )
            record = (state.get("files") or {}).get(state_key) if state_key else None
            owners = {
                str(owner) for owner in (record.get("owners") if isinstance(record, dict) else ()) or ()
            }
            if owners - gone:
                warnings.append(f"preserved loader file owned by other packages: {relative}")
                continue
            try:
                target = _existing_managed_file(game_dir, relative)
            except InstallError:
                continue
            if target is None:
                continue
            expected = str(entry.get("sha256") or "")
            if not expected:
                warnings.append(f"preserved loader file without a recorded digest: {relative}")
                continue
            try:
                digest = sha256_file(target)
            except OSError:
                warnings.append(f"could not verify loader file: {relative}")
                continue
            if digest != expected:
                warnings.append(f"preserved modified loader file: {relative}")
                continue
            try:
                target.unlink()
            except OSError:
                warnings.append(f"could not delete loader file: {relative}")

    @staticmethod
    def _remove_empty_managed_directories(game_dir: Path, roots: Iterable[str] = ()) -> None:
        for root_name in sorted(set(DEFAULT_MANAGED_ROOTS) | set(roots)):
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
