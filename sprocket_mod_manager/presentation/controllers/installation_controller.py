from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, cast

from .base import ApiController
from ..api_support import GamePathRequiredError
from ...application.install_queue import ACTIVE_STATES, InstallQueueEntry
from ...application.preparer import PlanPreparer
from ...application.private_install import prepare_private_package
from ...application.service import ModManagerService
from ...domain.compatibility import COMPATIBLE, Environment, release_verdict
from ...domain.errors import ModManagerError
from ...domain.models import RegistryPackage, ResolutionPlan
from ...infrastructure.config import effective_game_path
from ...infrastructure.melonloader import MelonLoaderInstallation, MelonLoaderRelease

LOGGER = logging.getLogger(__name__)


class InstallationController(ApiController):
    def _valid_game_path(self) -> Path:
        value = effective_game_path(self.config)
        path = Path(value).expanduser() if value else None
        if path is None or not (path / "Sprocket.exe").is_file():
            raise GamePathRequiredError("valid Sprocket game path is required")
        return path

    @staticmethod
    def _melonloader_data(
            installation: MelonLoaderInstallation,
            release: MelonLoaderRelease | None,
    ) -> dict[str, Any]:
        installed_version = str(installation.version) if installation.version else None
        latest_version = str(release.version) if release else None
        return {
            "installed": installation.installed,
            "installed_version": installed_version,
            "latest_version": latest_version,
            "update_available": bool(
                installation.installed
                and installation.version is not None
                and release is not None
                and installation.version < release.version
            ),
            "page_url": release.page_url if release else "",
            "asset_name": release.asset.name if release else "",
            "asset_size": release.asset.size if release else 0,
        }

    def get_environment(self, include_latest: bool = False) -> dict[str, Any]:
        """本机环境 + 环境自洽判定，以及监听线程维护的 `revision`。

        `revision` 变了就说明游戏目录里的东西（版本文件、加载器、Mods 目录）动过，界面该重画。

        `include_latest` 顺带查一次最新 MelonLoader（HTTP 有缓存）：没装加载器时拿它当环境值，
        否则「还没装」的机器永远看不出加载器跟游戏版本对不上。
        """
        try:
            snapshot = self.environment_snapshot()
            latest_version = snapshot.get("latest_loader")
            if include_latest:
                fetched = self._latest_melonloader_version()
                if fetched:
                    self._environment_monitor.note_latest_loader(fetched)
                    snapshot = self.environment_snapshot()
                    latest_version = fetched
            melonloader = dict(snapshot.get("melonloader") or {})
            used_version = melonloader.get("version") or latest_version
            return self._success(
                sprocket=dict(snapshot.get("sprocket") or {}),
                melonloader={
                    "installed": bool(melonloader.get("installed")),
                    "version": melonloader.get("version"),
                    "latest_version": latest_version,
                    "used_version": used_version,
                },
                environment=self.environment_payload(),
                revision=int(snapshot.get("revision") or 0),
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc, code="environment_failed")

    def _latest_melonloader_version(self) -> str | None:
        try:
            game_path = self._valid_game_path()
            _installation, release = self.melonloader.status(
                game_path, include_latest=True, refresh=False
            )
        except (ModManagerError, OSError, RuntimeError, ValueError):
            return None
        return str(release.version) if release else None

    def _current_environment(self) -> Environment:
        return self.current_environment()

    def preferred_version(self, service: ModManagerService, package: RegistryPackage) -> str:
        """这个包在当前环境下最该装的版本：兼容的里面最高的；一个都不兼容才退到最新。

        翻译包不参与环境判定（判定为「不适用」），于是自然退到「最新」。
        """
        try:
            releases = [
                release
                for release in service.github.releases(package)
                if service.github.install_assets(package, release)
            ]
        except (ModManagerError, OSError, RuntimeError, ValueError):
            return ""
        environment = self._current_environment()
        for release in releases:
            verdict = release_verdict(
                environment, category=package.category, dependencies=release.dependencies
            )
            if verdict == COMPATIBLE:
                return str(release.version)
        return str(releases[0].version) if releases else ""

    def get_melonloader_status(
            self,
            include_latest: bool = True,
            refresh: bool = False,
    ) -> dict[str, Any]:
        try:
            installation, release = self.melonloader.status(
                self._valid_game_path(),
                include_latest=bool(include_latest),
                refresh=bool(refresh),
            )
            return self._success(
                melonloader=self._melonloader_data(installation, release)
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "melonloader_status_failed"
            )
            return self._failure(exc, code=code)

    def install_melonloader(self, refresh: bool = False) -> dict[str, Any]:
        operation_started = False
        try:
            game_path = self._valid_game_path()
            with self._state_lock:
                if not self._melonloader_idle.is_set():
                    raise RuntimeError("a MelonLoader installation is already running")
                if any(entry.state in ACTIVE_STATES for entry in self.install_queue.snapshot()):
                    raise RuntimeError("wait for the mod install queue to finish before changing MelonLoader")
                if not self._mutation_lock.acquire(blocking=False):
                    raise RuntimeError("another game-directory operation is already running")
                self._melonloader_idle.clear()
                operation_started = True
            try:
                result = self.melonloader.install(game_path, refresh=bool(refresh))
            finally:
                if operation_started:
                    self._melonloader_idle.set()
                    self._mutation_lock.release()
                    operation_started = False
            installation = self.melonloader.detect(game_path)
            self._environment_monitor.invalidate()
            # 装完立刻按新的加载器版本查一遍表：跟不上这个游戏就明确告诉调用方（界面据此提示）。
            environment = self.environment_payload()
            if environment["state"] == "conflict":
                LOGGER.warning(
                    "installed MelonLoader %s does not support Sprocket %s",
                    installation.version,
                    environment["sprocket"],
                )
            return self._success(
                melonloader=self._melonloader_data(installation, result.release),
                compatibility=environment,
                files_installed=result.files_installed,
                sha256=result.sha256,
                publisher_verified=result.publisher_verified,
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "melonloader_install_failed"
            )
            return self._failure(exc, code=code)

    @staticmethod
    def _package(service: ModManagerService, package_id: str) -> RegistryPackage:
        if service.registry is None:
            raise RuntimeError("catalog is not loaded")
        return service.registry.get(package_id)

    @staticmethod
    def _plan_data(
            service: ModManagerService,
            package: RegistryPackage,
            plan: ResolutionPlan,
    ) -> dict[str, Any]:
        return {
            "id": package.id,
            "display_name": dict(package.display_name),
            "name": package.name,
            "replaces_autotranslator": any(
                item.package.install.get("mode") == "xunity-translation"
                for item in plan.packages
            ),
            "packages": [
                {
                    "id": item.package.id,
                    "display_name": dict(item.package.display_name),
                    "name": item.package.name,
                    "version": str(item.release.version),
                    "tag": item.release.tag,
                    "assets": [
                        asset.name
                        for asset in service.github.install_assets(item.package, item.release)
                    ],
                }
                for item in plan.packages
            ],
        }

    @staticmethod
    def _record_failure(failed: list[dict[str, str]], package_id: str, exc: Exception) -> None:
        """记下一个解析不了的模组：批量操作跳过它，继续处理剩下的。"""
        LOGGER.warning("skipping %s: %s", package_id, exc)
        failed.append({"id": package_id, "message": str(exc)})

    @staticmethod
    def _failure_message(failed: list[dict[str, str]]) -> str:
        """整批都没成时把原因原样带出去；只有一条就直接用它的话（不要包一层壳）。"""
        if len(failed) == 1:
            return failed[0]["message"]
        return " | ".join(f"{item['id']}: {item['message']}" for item in failed)

    def _version_range(
            self,
            service: ModManagerService,
            package: RegistryPackage,
            requested: str | None,
    ) -> str:
        """点名了就按点名的版本装，否则按当前环境挑一个（兼容里最高的）。"""
        version = str(requested or "").strip() or self.preferred_version(service, package)
        return f"={version}" if version else "*"

    def plan_install(
            self,
            package_ids: list[str],
            versions: dict[str, str] | None = None,
            include_installed: bool = False,
    ) -> dict[str, Any]:
        """`include_installed`：解析出来的版本就等于装着的那版时也把计划给我。

        界面靠这份计划才能开出安装对话框 —— 对话框里的版本选择器是「强行装新版」唯一的路，
        所以「已经装了当前能装的那版」不能把对话框整个挡掉。
        """
        try:
            game_path = self._valid_game_path()
            service = self._current_service()
            requested = {str(key): str(value) for key, value in (versions or {}).items()}
            installed = service.installed(game_path)
            plans: list[dict[str, Any]] = []
            resolved_plans: list[tuple[RegistryPackage, ResolutionPlan]] = []
            skipped: list[str] = []
            failed: list[dict[str, str]] = []
            for package_id in dict.fromkeys(str(item) for item in package_ids):
                try:
                    if any(package_id.startswith(str(item.get("server_id", "")) + ":") for item in
                           self._developer_server_entries()):
                        _client, plan, _downloaders = self._private_resolution(package_id)
                        root = plan.by_id()[package_id]
                        if not include_installed and installed.get(package_id, {}).get("version") == str(root.release.version):
                            skipped.append(package_id)
                        else:
                            plans.append(self._plan_data(service, root.package, plan))
                        continue
                    package = self._package(service, package_id)
                    # 版本由界面按环境挑好（点名），所以根包不再被环境淘汰；它的依赖照筛。
                    plan = service.resolve(
                        package.id,
                        self._version_range(service, package, requested.get(package_id)),
                        pinned=True,
                    )
                    root = plan.by_id()[package.id]
                    if not include_installed and installed.get(package.id, {}).get("version") == str(root.release.version):
                        skipped.append(package.id)
                        continue
                    plans.append(self._plan_data(service, package, plan))
                    resolved_plans.append((package, plan))
                except (ModManagerError, OSError, ValueError) as exc:
                    self._record_failure(failed, package_id, exc)
            covered = {
                item.package.id
                for _package, plan in resolved_plans
                for item in plan.packages
            }
            recommendations: dict[str, dict[str, Any]] = {}
            for package, _plan in resolved_plans:
                for recommendation_id in package.recommendations:
                    if recommendation_id in covered:
                        continue
                    if recommendation_id in recommendations:
                        recommendations[recommendation_id]["recommended_by"].append(package.id)
                        continue
                    try:
                        recommended = self._package(service, recommendation_id)
                        recommended_plan = service.resolve(
                            recommended.id,
                            self._version_range(service, recommended, requested.get(recommendation_id)),
                            pinned=True,
                        )
                    except ModManagerError:
                        continue
                    root = recommended_plan.by_id()[recommended.id]
                    if installed.get(recommended.id, {}).get("version") == str(root.release.version):
                        continue
                    data = self._plan_data(service, recommended, recommended_plan)
                    data["recommended_by"] = [package.id]
                    recommendations[recommended.id] = data
            if not plans and failed:
                raise ModManagerError(self._failure_message(failed))
            return self._success(
                plans=plans,
                recommendations=list(recommendations.values()),
                skipped=skipped,
                failed=failed,
                melonloader_installed=self.melonloader.detect(game_path).installed,
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "install_plan_failed"
            )
            return self._failure(exc, code=code)

    def enqueue_install(
            self,
            package_ids: list[str],
            allow_without_melonloader: bool = False,
            force_conflicts: bool = False,
            versions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            game_path = self._valid_game_path()
            if not self._melonloader_idle.is_set():
                raise RuntimeError("wait for the MelonLoader installation to finish")
            if (
                    not self.melonloader.detect(game_path).installed
                    and not bool(allow_without_melonloader)
            ):
                return self._failure(
                    RuntimeError("MelonLoader is not installed"),
                    code="melonloader_required",
                )
            service = self._current_service()
            requested = {str(key): str(value) for key, value in (versions or {}).items()}
            installed = service.installed(game_path)
            eligible: list[str] = []
            ranges: dict[str, str] = {}
            failed: list[dict[str, str]] = []
            for package_id in dict.fromkeys(str(item) for item in package_ids):
                try:
                    if any(package_id.startswith(str(item.get("server_id", "")) + ":") for item in
                           self._developer_server_entries()):
                        _entry, _client, manifest = self._private_package_source(package_id)
                        if installed.get(package_id, {}).get("version") != manifest.version:
                            eligible.append(package_id)
                        continue
                    package = self._package(service, package_id)
                    version_range = self._version_range(service, package, requested.get(package_id))
                    plan = service.resolve(package.id, version_range, pinned=True)
                    root = plan.by_id()[package.id]
                    if installed.get(package.id, {}).get("version") != str(root.release.version):
                        eligible.append(package.id)
                        ranges[package.id] = version_range
                except (ModManagerError, OSError, ValueError) as exc:
                    self._record_failure(failed, package_id, exc)
            if not eligible and failed:
                raise ModManagerError(self._failure_message(failed))
            with self._state_lock:
                if not self._melonloader_idle.is_set():
                    raise RuntimeError("wait for the MelonLoader installation to finish")
                added = self.install_queue.enqueue(
                    eligible,
                    game_path,
                    context=service,
                    force_conflicts=bool(force_conflicts),
                    version_ranges=ranges,
                )
            return self._success(added=[entry.task_id for entry in added], count=len(added), failed=failed)
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "install_enqueue_failed"
            )
            return self._failure(exc, code=code)

    def update_all(self, allow_without_melonloader: bool = False) -> dict[str, Any]:
        try:
            game_path = self._valid_game_path()
            if not self._melonloader_idle.is_set():
                raise RuntimeError("wait for the MelonLoader installation to finish")
            if (
                    not self.melonloader.detect(game_path).installed
                    and not bool(allow_without_melonloader)
            ):
                return self._failure(
                    RuntimeError("MelonLoader is not installed"),
                    code="melonloader_required",
                )
            service = self._current_service()
            installed = service.installed(game_path)
            updates: list[str] = []
            ranges: dict[str, str] = {}
            failed: list[dict[str, str]] = []
            public_ids = {item.id for item in service.registry.packages} if service.registry else set()
            for package_id, info in installed.items():
                if not info.get("requested"):
                    continue
                try:
                    if package_id not in public_ids:
                        _entry, _client, manifest = self._private_package_source(package_id)
                        if info.get("version") != manifest.version:
                            updates.append(package_id)
                        continue
                    package = self._package(service, package_id)
                    version_range = self._version_range(service, package, None)
                    plan = service.resolve(package.id, version_range, pinned=True)
                    latest = plan.by_id()[package_id].release.version
                    if info.get("version") != str(latest):
                        updates.append(package_id)
                        ranges[package_id] = version_range
                except (ModManagerError, OSError, ValueError) as exc:
                    self._record_failure(failed, package_id, exc)
            with self._state_lock:
                if not self._melonloader_idle.is_set():
                    raise RuntimeError("wait for the MelonLoader installation to finish")
                added = self.install_queue.enqueue(
                    updates, game_path, context=service, version_ranges=ranges
                )
            return self._success(added=[entry.task_id for entry in added], count=len(added), failed=failed)
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "update_failed"
            )
            return self._failure(exc, code=code)

    def remove(self, package_id: str) -> dict[str, Any]:
        try:
            with self._state_lock:
                if not self._melonloader_idle.is_set():
                    raise RuntimeError("wait for the MelonLoader installation to finish")
                if any(entry.state in ACTIVE_STATES for entry in self.install_queue.snapshot()):
                    raise RuntimeError("wait for the install queue to finish before removing packages")
                if not self._mutation_lock.acquire(blocking=False):
                    raise RuntimeError("another game-directory operation is already running")
            try:
                game_path = self._valid_game_path()
                service = self._current_service()
                installed = service.installed(game_path)
                private_ids = {
                    str(item.get("server_id", ""))
                    for item in self._developer_server_entries()
                }
                installed_info = installed.get(str(package_id), {})
                is_private_install = (
                        ":" in str(package_id)
                        and installed_info.get("repository", "") == ""
                )
                if is_private_install or any(str(package_id).startswith(server_id + ":") for server_id in private_ids):
                    removed, warnings = service._installer_for(game_path).remove(str(package_id), game_path)
                else:
                    package = self._package(service, str(package_id))
                    removed, warnings = service.remove(package.id, game_path)
            finally:
                self._mutation_lock.release()
            return self._success(removed=removed, warnings=warnings)
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "remove_failed"
            )
            return self._failure(exc, code=code)

    def _run_queued_install(
            self,
            entry: InstallQueueEntry,
            progress: Callable[[str], None],
    ) -> None:
        service = (
            cast(ModManagerService, entry.context)
            if entry.context is not None
            else self._current_service()
        )
        with self._mutation_lock:
            if any(
                    entry.package_id.startswith(str(item.get("server_id", "")) + ":")
                    for item in self._developer_server_entries()
            ):
                self._install_private_package(
                    entry.package_id,
                    entry.game_path,
                    progress,
                    force_conflicts=entry.force_conflicts,
                )
                return
            if entry.force_conflicts:
                service.install(
                    entry.package_id,
                    entry.game_path,
                    version_range=entry.version_range,
                    progress=progress,
                    force_conflicts=True,
                )
            else:
                service.install(
                    entry.package_id,
                    entry.game_path,
                    version_range=entry.version_range,
                    progress=progress,
                )

    def _install_private_package(
            self,
            package_id: str,
            game_path: Path,
            progress: Callable[[str], None],
            *,
            force_conflicts: bool,
    ) -> None:
        service = self._current_service()
        if service.registry is not None:
            _client, plan, downloaders = self._private_resolution(package_id)
            prepared = PlanPreparer(
                self.config_store.app_dir,
                service.http,
                service.github,
            ).prepare(plan, progress, private_downloaders=downloaders)
            try:
                service._installer_for(game_path).apply(
                    prepared,
                    game_path,
                    progress=progress,
                    force_conflicts=force_conflicts,
                )
            finally:
                PlanPreparer.discard(prepared)
            return
        _entry, client, manifest = self._private_package_source(package_id)
        user_id = str(self.config.get("github_user_id", "") or "").strip()
        prepared = prepare_private_package(
            self.config_store.app_dir,
            client,
            package_id,
            manifest,
            user_id,
            progress,
        )
        try:
            self._current_service()._installer_for(game_path).apply(
                prepared,
                game_path,
                progress=progress,
                force_conflicts=force_conflicts,
            )
        finally:
            PlanPreparer.discard(prepared)

    def _queue_data(self) -> list[dict[str, Any]]:
        return [
            {
                "task_id": entry.task_id,
                "package_id": entry.package_id,
                "state": entry.state,
                "message": entry.message,
                "error_code": entry.error_code,
            }
            for entry in self.install_queue.snapshot()
        ]

    def get_queue(self) -> dict[str, Any]:
        return self._success(
            entries=self._queue_data(),
            close_pending=self._close_pending,
        )

    def cancel_queue_item(self, task_id: str) -> dict[str, Any]:
        return self._success(canceled=self.install_queue.cancel(str(task_id)))

    def clear_finished(self) -> dict[str, Any]:
        self.install_queue.clear_finished()
        return self._success(entries=self._queue_data())
