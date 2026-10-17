from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Iterable, cast

from .base import ApiController
from ..api_support import GamePathRequiredError
from ...application.data_hub import KEY_ENVIRONMENT, KEY_INSTALLED, KEY_LOADERS, KEY_QUEUE
from ...application.install_queue import ACTIVE_STATES, InstallQueueEntry
from ...application.planning import ACTION_INSTALL, ChangePlanner, ChangeRequest
from ...application.preparer import PlanPreparer, satisfied_versions
from ...application.service import ModManagerService
from ...domain.compatibility import CapabilityEnvironment, loader_table_decision, release_verdict
from ...domain.errors import ModManagerError
from ...domain.models import RegistryPackage, ResolutionPlan
from ...domain.registry import Registry
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
            "companions": [dict(item) for item in package.companions],
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
                    version_range=self._version_range(None),
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
            package_id = service.modloader_id(str(identifier), game_path)
            with self._state_lock:
                if not self._loader_idle.is_set():
                    raise RuntimeError("a modloader installation is already running")
                if any(entry.state in ACTIVE_STATES for entry in self.install_queue.snapshot()):
                    raise RuntimeError("wait for the mod install queue to finish before changing modloaders")
                if not self._mutation_lock.acquire(blocking=False):
                    raise RuntimeError("another game-directory operation is already running")
            try:
                removed, warnings = service.remove(package_id, game_path)
            finally:
                self._mutation_lock.release()
            self._environment_monitor.invalidate()
            # 卸载加载器会把它供给的目录一起搬走：四份读数都变了。
            self.data_changed(KEY_INSTALLED, KEY_ENVIRONMENT, KEY_LOADERS, KEY_QUEUE)
            items = self._modloader_items(service, game_path, [package_id])
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
            installing: Iterable[str],
            installed: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """装这一批会让哪些已装加载器被交还：确认框先把它们列出来。

        加载器可能是被依赖带进来的（模组依赖 BepInEx，BepInEx 就顶掉官方 MelonLoader），所以看的是
        **这一批里的每个加载器**，不只是用户点的那个包。这一批里的包不算：它们是这次要装的。判定与
        落盘前那次交还用同一个口径（`capability_conflicts`），确认框里说会卸载的就是实际会卸载的。
        """
        registry = service.registry
        if registry is None:
            return []
        wanted = {str(item) for item in installing}
        displaced: list[str] = []
        for package_id in sorted(wanted):
            if not registry.has_package(package_id) or not registry.get(package_id).is_loader:
                continue
            for other in service.capability_conflicts(package_id, installed):
                if other not in wanted and other not in displaced:
                    displaced.append(other)
        entries: list[dict[str, Any]] = []
        for package_id in displaced:
            try:
                other = registry.get(package_id)
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

    @staticmethod
    def _version_range(requested: str | None) -> str:
        """点名了就钉死那一版；没点名就给一段空区间。

        空区间让求解器按环境**和世界**（已装的、同批的）自己挑版本 —— 界面默认推荐的那一版
        由候选顺序保证优先，只有它和世界不相容时才让路。
        """
        version = str(requested or "").strip()
        return f"={version}" if version else "*"

    def _named_roots(
            self,
            service: ModManagerService,
            package_ids: list[str],
            requested: dict[str, str],
            failed: list[dict[str, str]],
    ) -> tuple[dict[str, str], list[str]]:
        """这批要装的根：版本区间，以及点了名（因此豁免环境判定）的那些。"""
        ranges: dict[str, str] = {}
        pinned: list[str] = []
        for package_id in dict.fromkeys(str(item) for item in package_ids):
            try:
                package = self._package(service, package_id)
            except (ModManagerError, OSError, ValueError) as exc:
                self._record_failure(failed, package_id, exc)
                continue
            ranges[package.id] = self._version_range(requested.get(package.id))
            if ranges[package.id] != "*":
                pinned.append(package.id)
        return ranges, pinned

    def _solve_together(
            self,
            service: ModManagerService,
            roots: dict[str, str],
            pinned: list[str],
            installed_versions: dict[str, str],
            failed: list[dict[str, str]],
    ) -> tuple[ResolutionPlan | None, dict[str, str]]:
        """把这批根和世界一起解。

        一起解不出来时分两步：先逐个解，把「自己就装不上」的那些挑出来报清楚；再把剩下的
        一起解一次。整批都过不去才整体失败 —— 报出来的原因是「哪一个装不上」，不是「整批失败」。
        """
        if not roots:
            return None, roots
        try:
            plan = service.resolve_all(roots, pinned=pinned, installed=installed_versions)
        except (ModManagerError, OSError, ValueError) as exc:
            for package_id in roots:
                try:
                    service.resolve_all(
                        {package_id: roots[package_id]},
                        pinned=[package_id] if package_id in pinned else [],
                        installed=installed_versions,
                    )
                except (ModManagerError, OSError, ValueError) as single:
                    self._record_failure(failed, package_id, single)
            broken = {item["id"] for item in failed}
            if all(package_id in broken for package_id in roots):
                raise ModManagerError(self._failure_message(failed)) from exc
            remaining = {key: value for key, value in roots.items() if key not in broken}
            plan = service.resolve_all(
                remaining,
                pinned=[item for item in pinned if item in remaining],
                installed=installed_versions,
            )
            return plan, remaining
        return plan, roots

    def _change_request(
            self,
            package_ids: list[str],
            choices: dict[str, str] | None,
            reinstall: bool,
            force: bool,
    ) -> tuple[ModManagerService, Path, ChangeRequest]:
        game_path = self._valid_game_path()
        service = self._current_service()
        request = ChangeRequest(
            package_ids=tuple(str(item) for item in package_ids),
            choices={str(key): str(value) for key, value in (choices or {}).items()},
            reinstall=bool(reinstall),
            force=bool(force),
        )
        # 只卸载的请求读安装记录就算得出来：目录没加载也照样能卸。
        if service.registry is None and not request.removes_only():
            raise RuntimeError("catalog is not loaded")
        return service, game_path, request

    def _change_plan(
            self,
            service: ModManagerService,
            game_path: Path,
            request: ChangeRequest,
    ) -> dict[str, Any]:
        plan = ChangePlanner(
            registry=service.registry or Registry([]),
            github=service.github,
            environment=self.current_environment(),
            state=service.installed(game_path),
            modloaders=service.modloader_ids(),
        ).plan(request)
        # 这一批会让哪些已装加载器被交还：计划页先把它们摆出来（模组把 BepInEx 当依赖带进来时，
        # 顶掉的可能是官方 MelonLoader）。
        plan["displaces"] = self._displaced_loaders(
            service, plan.get("install", {}), service.installed(game_path)
        )
        return plan

    def plan_changes(
            self,
            package_ids: list[str],
            choices: dict[str, str] | None = None,
            reinstall: bool = False,
            force: bool = False,
    ) -> dict[str, Any]:
        """把这批操作算成一份计划：每行的动作、版本、兼容性与红行原因。

        装不上不是错误：冲突的包标红出局，结果可以是空安装列表 —— 计划页照开。只有「现在
        根本做不了」（没配游戏路径、注册表没加载）才返回失败。
        """
        try:
            service, game_path, request = self._change_request(
                package_ids, choices, reinstall, force
            )
            return self._success(**self._change_plan(service, game_path, request))
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "install_plan_failed"
            )
            return self._failure(exc, code=code)

    def apply_changes(
            self,
            package_ids: list[str],
            choices: dict[str, str] | None = None,
            reinstall: bool = False,
            force: bool = False,
    ) -> dict[str, Any]:
        """执行计划页确认过的那份计划。

        按请求**重算一次**再执行：客户端不传版本、也不传缺口形状，装下去的就是求解器现在解出来
        的那一版（世界与索引在这两步之间变了的话，以执行时的解为准）。卸载同步做、不进下载队列；
        安装进队列，照那一行的缺口形状落盘。
        """
        try:
            service, game_path, request = self._change_request(
                package_ids, choices, reinstall, force
            )
            plan = self._change_plan(service, game_path, request)
            if plan["no_solution"]:
                raise ModManagerError(str(plan["message"]))
            removing = list(plan["remove"])
            with self._state_lock:
                if not self._loader_idle.is_set():
                    raise RuntimeError("wait for the modloader installation to finish")
                if removing:
                    # 卸载不进下载队列：它是同步的一次性动作，做完再排安装。
                    service.remove_many(removing, game_path)
                    self._environment_monitor.invalidate()
                install_lines = [
                    line for line in plan["lines"]
                    if line["action"] == ACTION_INSTALL and line["named"]
                ]
                gaps = {
                    line["id"]: (
                        tuple((line["id"], str(item)) for item in line["ignored"]),
                        tuple(plan["excluded"]),
                    )
                    for line in install_lines
                }
                added = self.install_queue.enqueue(
                    [line["id"] for line in install_lines],
                    game_path,
                    context=service,
                    force_conflicts=bool(force),
                    version_ranges={
                        line["id"]: f"={line['version']}" for line in install_lines
                    },
                    gaps=gaps,
                )
            self.data_changed(KEY_INSTALLED, KEY_ENVIRONMENT, KEY_QUEUE, KEY_LOADERS)
            return self._success(
                added=[entry.task_id for entry in added],
                count=len(added),
                removed=removing,
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "install_apply_failed"
            )
            return self._failure(exc, code=code)

    def enqueue_install(
            self,
            package_ids: list[str],
            force_conflicts: bool = False,
            versions: dict[str, str] | None = None,
            reinstall: bool = False,
    ) -> dict[str, Any]:
        """把要装的包排队。

        `reinstall`（或 `force_conflicts`）是「用户明确要求重装」：解析出来的版本就算与装着的那版
        相同也照常排队 —— 重装要的是把文件覆盖回去，不是换版本。两个都不给时，同版本视为
        「已经装了」，不入队。
        """
        try:
            game_path = self._valid_game_path()
            if not self._loader_idle.is_set():
                raise RuntimeError("wait for the modloader installation to finish")
            service = self._current_service()
            requested = {str(key): str(value) for key, value in (versions or {}).items()}
            installed = service.installed(game_path)
            installed_versions = service.installed_versions(installed)
            explicit = bool(reinstall or force_conflicts)
            failed: list[dict[str, str]] = []
            roots, pinned = self._named_roots(service, package_ids, requested, failed)
            eligible: list[str] = []
            ranges: dict[str, str] = {}
            if roots:
                plan, roots = self._solve_together(
                    service, roots, pinned, installed_versions, failed
                )
                by_id = plan.by_id()
                for package_id in roots:
                    # 入队时就把**一起解出来的那一版**钉死：队列执行时不再改主意，用户确认过的
                    # 那一组版本就是最终装上的那一组。
                    solved = str(by_id[package_id].release.version)
                    if explicit or installed.get(package_id, {}).get("version") != solved:
                        eligible.append(package_id)
                        ranges[package_id] = f"={solved}"
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
                removed, warnings = service.remove(str(package_id), game_path)
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
                # 队列执行的是用户在计划页确认过的那一行：这里只按点名的那一版落盘，
                # 世界只作偏好 —— 冲突已经在计划那一步摆清楚并由用户拍板了。
                if entry.force_conflicts:
                    service.install(
                        entry.package_id,
                        entry.game_path,
                        version_range=entry.version_range,
                        progress=progress,
                        force_conflicts=True,
                        constrain_world=False,
                        ignore_edges=entry.ignore_edges,
                        excluded=entry.excluded,
                    )
                else:
                    service.install(
                        entry.package_id,
                        entry.game_path,
                        version_range=entry.version_range,
                        progress=progress,
                        constrain_world=False,
                        ignore_edges=entry.ignore_edges,
                        excluded=entry.excluded,
                    )
            finally:
                # 队列装的可能就是加载器：装完环境读数必须重来一次；已安装读数与队列本身也变了，
                # 让数据层自己重算并推送 —— 不在返回值里另带一份。
                self._environment_monitor.invalidate()
                self.data_changed(KEY_INSTALLED, KEY_ENVIRONMENT, KEY_QUEUE, KEY_LOADERS)

    def _queue_data(self) -> list[dict[str, Any]]:
        """队列里的每一条；某一条读不出来只跳过它自己，别让整页队列读数消失。"""
        try:
            entries = self.install_queue.snapshot()
        except Exception as exc:
            LOGGER.warning("queue snapshot failed error=%s", exc)
            return []
        data: list[dict[str, Any]] = []
        for entry in entries:
            try:
                data.append(
                    {
                        "task_id": entry.task_id,
                        "package_id": entry.package_id,
                        "state": entry.state,
                        "message": entry.message,
                        "error_code": entry.error_code,
                    }
                )
            except Exception as exc:
                LOGGER.warning("queue entry failed error=%s", exc)
                data.append(
                    {
                        "task_id": "",
                        "package_id": "",
                        "state": "failed",
                        "message": f"queue entry unreadable: {exc}",
                        "error_code": "queue_entry_unreadable",
                    }
                )
        return data

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
