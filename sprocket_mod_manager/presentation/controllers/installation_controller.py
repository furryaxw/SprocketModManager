from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Iterable, cast

from .base import ApiController
from ..api_support import GamePathRequiredError
from ...application.data_hub import KEY_ENVIRONMENT, KEY_INSTALLED, KEY_LOADERS, KEY_QUEUE
from ...application.install_queue import ACTIVE_STATES, InstallQueueEntry
from ...application.preparer import PlanPreparer, satisfied_versions
from ...application.private_install import prepare_private_package
from ...application.service import ModManagerService
from ...domain.compatibility import COMPATIBLE, CapabilityEnvironment, loader_table_decision, release_verdict
from ...domain.errors import ModManagerError
from ...domain.models import RegistryPackage, ResolutionPlan
from ...domain.semver import Version
from ...infrastructure.config import effective_game_path
from ...utilities.processes import sprocket_is_running, terminate_sprocket

LOGGER = logging.getLogger(__name__)


class InstallationController(ApiController):
    def _valid_game_path(self) -> Path:
        value = effective_game_path(self.config)
        path = Path(value).expanduser() if value else None
        if path is None or not (path / "Sprocket.exe").is_file():
            raise GamePathRequiredError("valid Sprocket game path is required")
        return path

    def get_environment(self, include_latest: bool = False) -> dict[str, Any]:
        """本机环境 + 环境自洽判定，以及监听线程维护的 `revision`。

        `revision` 变了就说明游戏目录里的东西（版本文件、加载器、Mods 目录）动过，界面该重画。

        `loaders` 每个加载器一条：装的是哪版、能装的最新版、判定用的那版。`include_latest`
        决定要不要现去查最新版（HTTP 有缓存）；不查就用监听缓存里上次记下的那份，没记过就是空。
        """
        try:
            snapshot = self.environment_snapshot()
            latest_loaders = dict(snapshot.get("latest_loaders") or {})
            if include_latest:
                fetched = self._latest_loader_versions()
                if fetched:
                    self._environment_monitor.note_latest_loaders(fetched)
                    snapshot = self.environment_snapshot()
                    latest_loaders = fetched
            installed_loaders = dict(snapshot.get("loaders") or {})
            loaders: dict[str, dict[str, Any]] = {}
            for loader_id in sorted(set(installed_loaders) | set(latest_loaders)):
                info = installed_loaders.get(loader_id) or {}
                version = str(info.get("version") or "")
                latest = str(latest_loaders.get(loader_id) or "")
                loaders[loader_id] = {
                    "installed": bool(info.get("installed")),
                    "version": version or None,
                    "latest_version": latest or None,
                    "used_version": (version or latest) or None,
                }
            return self._success(
                sprocket=dict(snapshot.get("sprocket") or {}),
                loaders=loaders,
                environment=self.environment_payload(),
                sprocket_running=sprocket_is_running(self._game_path_or_none()),
                revision=int(snapshot.get("revision") or 0),
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc, code="environment_failed")

    def _latest_loader_versions(self) -> dict[str, str]:
        """每个加载器能装的最新版；拉不到就跳过那个包，不抛给调用方。"""
        try:
            game_path = self._valid_game_path()
            status = self._current_service().modloader_status(game_path)
        except (ModManagerError, OSError, RuntimeError, ValueError):
            return {}
        return {
            loader_id: str(info.get("latest_version") or "")
            for loader_id, info in status.items()
            if info.get("latest_version")
        }

    def _current_environment(self) -> CapabilityEnvironment:
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

    def get_modloaders(self) -> dict[str, Any]:
        """注册表里所有加载器包：装没装、能不能更新、在当前环境下能不能跑。"""
        try:
            service = self._current_service()
            return self._success(
                modloaders=self._modloader_items(service, self._game_path_or_none())
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc, code="modloaders_failed")

    @staticmethod
    def _is_newer(latest: str, version: str) -> bool:
        if not latest or not version:
            return False
        try:
            return Version.parse(latest) > Version.parse(version)
        except ValueError:
            return False

    def _modloader_item(
            self,
            service: ModManagerService,
            package: RegistryPackage,
            status: dict[str, Any],
            installed_info: dict[str, Any],
            environment: CapabilityEnvironment,
    ) -> dict[str, Any]:
        # 「装没装 / 哪一版」只认 `modloader_status`（`runtime_states` 一处给出的口径）：记录在案的
        # 包与磁盘上检测到的运行时都在场，卡片和侧栏因此不会各说各话。
        version = str(status.get("version") or "")
        latest = str(status.get("latest_version") or "")
        installed = bool(status.get("installed"))
        compatible = "unknown"
        registry = service.registry
        table = registry.provider_table if registry is not None else {}
        game_capability = environment.game_id
        game_version = environment.capability_version(game_capability)
        try:
            for release in service.github.releases(package):
                if not service.github.install_assets(package, release):
                    continue
                decision = loader_table_decision(
                    table, package.id, game_capability, game_version, str(release.version)
                )
                compatible = (
                    decision[0]
                    if decision is not None
                    else release_verdict(
                        environment,
                        category=package.category,
                        dependencies=release.dependencies,
                    )
                )
                break
        except (ModManagerError, OSError, RuntimeError, ValueError):
            compatible = "unknown"
        files = [item for item in (installed_info.get("files") or ()) if isinstance(item, str)]
        return {
            "id": package.id,
            "name": package.name,
            "display_name": dict(package.display_name),
            "description": dict(package.description),
            "repository": package.repository,
            "page_url": f"https://github.com/{package.repository}",
            "category": package.category,
            "tags": list(package.tags),
            "installed": installed,
            "installed_version": version,
            "latest_version": latest,
            "update_available": installed and self._is_newer(latest, version),
            "compatible": compatible,
            "supply": [
                {"type": file_type, "directory": target}
                for file_type, target in package.supply.items()
            ],
            "dependencies": [dict(item) for item in package.dependencies],
            "recommendations": list(package.recommendations),
            "files": len(files),
        }

    def _modloader_items(
            self,
            service: ModManagerService,
            game_path: Path | None,
            package_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        registry = service.registry
        if registry is None:
            return []
        wanted = set(package_ids) if package_ids is not None else None
        status = service.modloader_status(game_path)
        installed = service.installed(game_path) if game_path is not None else {}
        environment = service.environment or self._current_environment()
        return [
            self._modloader_item(
                service,
                package,
                status.get(package.id, {}),
                installed.get(package.id, {}),
                environment,
            )
            for package in registry.modloaders()
            if wanted is None or package.id in wanted
        ]

    def _resolve_modloader(self, service: ModManagerService, identifier: str) -> RegistryPackage:
        if service.registry is None:
            raise RuntimeError("catalog is not loaded")
        package = service.registry.resolve_identifier(str(identifier))
        if not package.is_modloader:
            raise ModManagerError(f"{package.id} is not a modloader")
        return package

    def install_modloader(self, identifier: str, refresh: bool = False) -> dict[str, Any]:
        """按普通包的路子装一个加载器：解析 → 准备 → 应用，版本在解析时就钉死。

        `refresh` 让这次解析绕过 Release 缓存重新拉一遍，其余时候用缓存。
        """
        try:
            game_path = self._valid_game_path()
            service = self._current_service()
            package = self._resolve_modloader(service, identifier)
            if refresh:
                service.github.releases(package, refresh=True)
            with self._state_lock:
                if not self._loader_idle.is_set():
                    raise RuntimeError("a modloader installation is already running")
                if any(entry.state in ACTIVE_STATES for entry in self.install_queue.snapshot()):
                    raise RuntimeError("wait for the mod install queue to finish before changing modloaders")
                if not self._mutation_lock.acquire(blocking=False):
                    raise RuntimeError("another game-directory operation is already running")
                self._loader_idle.clear()
            try:
                service.install(
                    package.id,
                    game_path,
                    version_range=self._version_range(service, package, None),
                    progress=None,
                )
            finally:
                self._loader_idle.set()
                self._mutation_lock.release()
            self._environment_monitor.invalidate()
            # 装完一个加载器：四份读数都变了 —— 让数据层自己重算并推给界面。
            self.data_changed(KEY_INSTALLED, KEY_ENVIRONMENT, KEY_LOADERS, KEY_QUEUE)
            items = self._modloader_items(service, game_path, [package.id])
            item = items[0] if items else {}
            return self._success(
                modloader=item,
                # 加载器不逐文件记账，写入数由这次安装自己报出来。
                files_installed=getattr(service, "last_install_files", 0),
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "modloader_install_failed"
            )
            return self._failure(exc, code=code)

    def remove_modloader(self, identifier: str) -> dict[str, Any]:
        try:
            game_path = self._valid_game_path()
            service = self._current_service()
            package = self._resolve_modloader(service, identifier)
            with self._state_lock:
                if not self._loader_idle.is_set():
                    raise RuntimeError("a modloader installation is already running")
                if any(entry.state in ACTIVE_STATES for entry in self.install_queue.snapshot()):
                    raise RuntimeError("wait for the mod install queue to finish before changing modloaders")
                if not self._mutation_lock.acquire(blocking=False):
                    raise RuntimeError("another game-directory operation is already running")
            try:
                removed, warnings = service.remove(package.id, game_path)
            finally:
                self._mutation_lock.release()
            self._environment_monitor.invalidate()
            # 卸载加载器会把它供给的目录一起搬走：四份读数都变了。
            self.data_changed(KEY_INSTALLED, KEY_ENVIRONMENT, KEY_LOADERS, KEY_QUEUE)
            items = self._modloader_items(service, game_path, [package.id])
            return self._success(
                modloader=items[0] if items else {},
                removed=removed,
                warnings=warnings,
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "modloader_remove_failed"
            )
            return self._failure(exc, code=code)

    @staticmethod
    def _package(service: ModManagerService, package_id: str) -> RegistryPackage:
        if service.registry is None:
            raise RuntimeError("catalog is not loaded")
        return service.registry.get(package_id)

    def _displaced_loaders(
            self,
            service: ModManagerService,
            plan: ResolutionPlan,
            installed: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """装这个计划会让哪些已装加载器被交还：确认框先把它们列出来。

        加载器可能是被依赖带进来的（模组依赖 BepInEx，BepInEx 就顶掉官方 MelonLoader），所以看的是
        **计划里的每个加载器**，不只是用户点的那个包。计划里的包不算：它们是这次要装的。判定与落盘前
        那次交还用同一个口径（`capability_conflicts`），确认框里说会卸载的就是实际会卸载的。
        """
        if service.registry is None:
            return []
        installing = {item.package.id for item in plan.packages}
        displaced: list[str] = []
        for item in plan.packages:
            if not item.package.is_loader:
                continue
            for package_id in service.capability_conflicts(item.package.id, installed):
                if package_id not in installing and package_id not in displaced:
                    displaced.append(package_id)
        entries: list[dict[str, Any]] = []
        for package_id in displaced:
            if package_id in installing:
                continue
            try:
                other = service.registry.get(package_id)
            except ModManagerError:
                continue
            info = installed.get(package_id) or {}
            entries.append({
                "id": other.id,
                "name": other.name,
                "display_name": dict(other.display_name),
                "version": str(info.get("version") or ""),
            })
        return entries

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
                "xunity:translation" in item.package.replace_types()
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
                        _client, plan, _downloaders = self._private_resolution(
                            package_id, frozenset(installed)
                        )
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
                        installed=frozenset(installed),
                    )
                    root = plan.by_id()[package.id]
                    if not include_installed and installed.get(package.id, {}).get("version") == str(root.release.version):
                        skipped.append(package.id)
                        continue
                    data = self._plan_data(service, package, plan)
                    data["displaces"] = self._displaced_loaders(service, plan, installed)
                    plans.append(data)
                    resolved_plans.append((package, plan))
                except (ModManagerError, OSError, ValueError) as exc:
                    self._record_failure(failed, package_id, exc)
            covered = {
                item.package.id
                for _package, plan in resolved_plans
                for item in plan.packages
            }
            recommendations: dict[str, dict[str, Any]] = {}
            # 推荐来自计划里的每个包：依赖带进来的加载器也有自己的推荐（BepInEx 推荐兼容补丁），
            # 它们和用户点的那个包一样会一起装上，所以推荐要一起给出来。
            for _package, plan in resolved_plans:
                for item in plan.packages:
                    recommending = item.package
                    for recommendation_id in recommending.recommendations:
                        if recommendation_id in covered:
                            continue
                        if recommendation_id in recommendations:
                            recommendations[recommendation_id]["recommended_by"].append(recommending.id)
                            continue
                        try:
                            recommended = self._package(service, recommendation_id)
                            recommended_plan = service.resolve(
                                recommended.id,
                                self._version_range(service, recommended, requested.get(recommendation_id)),
                                pinned=True,
                                installed=frozenset(installed),
                            )
                        except ModManagerError:
                            continue
                        root = recommended_plan.by_id()[recommended.id]
                        if installed.get(recommended.id, {}).get("version") == str(root.release.version):
                            continue
                        data = self._plan_data(service, recommended, recommended_plan)
                        data["recommended_by"] = [recommending.id]
                        recommendations[recommended.id] = data
            if not plans and failed:
                raise ModManagerError(self._failure_message(failed))
            return self._success(
                plans=plans,
                recommendations=list(recommendations.values()),
                skipped=skipped,
                failed=failed,
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
            force_conflicts: bool = False,
            versions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            game_path = self._valid_game_path()
            if not self._loader_idle.is_set():
                raise RuntimeError("wait for the modloader installation to finish")
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
                    plan = service.resolve(
                        package.id,
                        version_range,
                        pinned=True,
                        installed=frozenset(installed),
                    )
                    root = plan.by_id()[package.id]
                    if installed.get(package.id, {}).get("version") != str(root.release.version):
                        eligible.append(package.id)
                        ranges[package.id] = version_range
                except (ModManagerError, OSError, ValueError) as exc:
                    self._record_failure(failed, package_id, exc)
            if not eligible and failed:
                raise ModManagerError(self._failure_message(failed))
            with self._state_lock:
                if not self._loader_idle.is_set():
                    raise RuntimeError("wait for the modloader installation to finish")
                added = self.install_queue.enqueue(
                    eligible,
                    game_path,
                    context=service,
                    force_conflicts=bool(force_conflicts),
                    version_ranges=ranges,
                )
            self.data_changed(KEY_QUEUE)
            return self._success(added=[entry.task_id for entry in added], count=len(added), failed=failed)
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "install_enqueue_failed"
            )
            return self._failure(exc, code=code)

    def update_all(self) -> dict[str, Any]:
        try:
            game_path = self._valid_game_path()
            if not self._loader_idle.is_set():
                raise RuntimeError("wait for the modloader installation to finish")
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
                    plan = service.resolve(
                        package.id,
                        version_range,
                        pinned=True,
                        installed=frozenset(installed),
                    )
                    latest = plan.by_id()[package_id].release.version
                    if info.get("version") != str(latest):
                        updates.append(package_id)
                        ranges[package_id] = version_range
                except (ModManagerError, OSError, ValueError) as exc:
                    self._record_failure(failed, package_id, exc)
            with self._state_lock:
                if not self._loader_idle.is_set():
                    raise RuntimeError("wait for the modloader installation to finish")
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
                if not self._loader_idle.is_set():
                    raise RuntimeError("wait for the modloader installation to finish")
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
                # 交还加载器的树会改掉加载器清单：环境读数必须重来一次，否则左下角留着旧读数。
                self._environment_monitor.invalidate()
                # 卸载会连带搬走供给目录里的模组：四份读数都变了。
                self.data_changed(KEY_INSTALLED, KEY_ENVIRONMENT, KEY_LOADERS, KEY_QUEUE)
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

    def kill_sprocket(self) -> dict[str, Any]:
        """结束当前游戏路径里的 Sprocket：改模组文件前不用自己去关游戏。"""
        try:
            game_path = self._game_path_or_none()
            if game_path is None:
                raise GamePathRequiredError("valid Sprocket game path is required")
            return self._success(killed=terminate_sprocket(game_path))
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "sprocket_kill_failed"
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
            try:
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
            finally:
                # 队列装的可能就是加载器：装完环境读数必须重来一次；已安装读数与队列本身也变了，
                # 让数据层自己重算并推送 —— 不在返回值里另带一份。
                self._environment_monitor.invalidate()
                self.data_changed(KEY_INSTALLED, KEY_ENVIRONMENT, KEY_QUEUE, KEY_LOADERS)

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
            installed = service.installed(game_path)
            _client, plan, downloaders = self._private_resolution(
                package_id, frozenset(installed)
            )
            prepared = PlanPreparer(
                self.config_store.app_dir,
                service.http,
                service.github,
            ).prepare(
                plan,
                progress,
                private_downloaders=downloaders,
                satisfied=satisfied_versions(installed),
            )
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
        canceled = self.install_queue.cancel(str(task_id))
        self.data_changed(KEY_QUEUE)
        return self._success(canceled=canceled)

    def clear_completed(self) -> dict[str, Any]:
        self.install_queue.clear_completed()
        self.data_changed(KEY_QUEUE)
        return self._success()
