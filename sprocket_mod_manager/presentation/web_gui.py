from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .api_constants import MANAGER_REPOSITORY
from .controllers import CatalogController, InstallationController, PrivateDistributionController, SettingsController
from ..application.identifiers import detected_capabilities, log_sources, mod_directory_paths, runtime_states
from ..application.install_queue import InstallQueue
from ..application.service import ModManagerService
from ..domain.compatibility import DEFAULT_GAME_CAPABILITY, CapabilityEnvironment
from ..domain.errors import ModManagerError
from ..domain.models import ReleaseInfo, VERSION_TEMPLATE
from ..domain.semver import Version
from ..infrastructure.config import ConfigStore, detect_language, effective_game_path, effective_github_proxy_url, \
    effective_proxy_url
from ..infrastructure.credential_store import CredentialStore
from ..infrastructure.desktop import open_directory
from ..infrastructure.app_logging import manager_log_path
from ..infrastructure.providers_cache import read_providers_table
from ..infrastructure.environment_monitor import (
    MOD_DIRECTORIES,
    EnvironmentMonitor,
    game_environment_fingerprint,
)
from ..infrastructure.game_version import GameVersion, read_game_version
from ..infrastructure.log_upload import upload_log_file
from ..infrastructure.private_servers import PrivateCatalogCache
from ..infrastructure.self_update import (
    can_self_update,
    download_update,
    frozen_executable,
    launch_self_update,
    staged_executable,
    update_from_release,
)

LOGGER = logging.getLogger(__name__)
LOG_UPLOAD_ENDPOINT = "https://paste.furryaxw.top/api/q/"


def _flush_logs() -> None:
    """退出前把日志刷盘：`os._exit` 不会帮我们 flush。"""
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except (OSError, ValueError):
            continue


class ClientApi:
    def __init__(
            self,
            version: str,
            *,
            app_dir: Path | None = None,
            service_factory: Callable[[Path], ModManagerService] = ModManagerService,
            debug_override: bool = False,
    ) -> None:
        self.version = version
        self._debug_override = debug_override
        self.config_store = ConfigStore(app_dir)
        LOGGER.debug("ClientApi init: config store created app_dir=%s", self.config_store.app_dir)
        scope = hashlib.sha256(str(self.config_store.app_dir).encode("utf-8")).hexdigest()[:16]
        self.credentials = CredentialStore(f"SprocketModManager/{scope}")
        self.config = self.config_store.load()
        LOGGER.debug("ClientApi init: config loaded")
        self.private_catalog_cache = PrivateCatalogCache(self.config_store.app_dir)
        self._service_factory = service_factory
        self.service = service_factory(self.config_store.app_dir)
        LOGGER.debug("ClientApi init: service created")
        self._configure_service_network(self.service)
        self.latest: dict[str, ReleaseInfo | None] = {}
        self._catalog_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._window: Any = None
        self._close_pending = False
        self._destroy_scheduled = False
        self._github_device: dict[str, Any] | None = None
        self._github_access_token = ""
        self._gist_conflicts: list[dict[str, Any]] = []
        self._mutation_lock = threading.Lock()
        self._loader_idle = threading.Event()
        self._loader_idle.set()
        self._settings_controller = SettingsController(self)
        self._private_controller = PrivateDistributionController(self)
        self._catalog_controller = CatalogController(self)
        self._installation_controller = InstallationController(self)
        self._controllers = (
            self._settings_controller,
            self._private_controller,
            self._catalog_controller,
            self._installation_controller,
        )
        self.install_queue = InstallQueue(self._run_queued_install)
        LOGGER.debug("ClientApi init: install queue created")
        # 指纹要覆盖活跃标识符的目录（含桥接加载器的 `MLLoader/Mods`）。目录名单随环境读数刷新，
        # 指纹每秒只读这份缓存。
        self._mod_directories: tuple[str, ...] = MOD_DIRECTORIES
        self._environment_monitor = EnvironmentMonitor(
            self._read_environment,
            lambda: game_environment_fingerprint(self._game_path_or_none(), self._mod_directories),
        )
        # 供给表（加载器包↔游戏）在内存里留一份，省掉每秒问一次时读盘。
        self._providers_table_cache: dict[str, Any] | None = None
        self._providers_table_read = False
        LOGGER.info("Client API initialized version=%s", self.version)

    def environment_snapshot(self) -> dict[str, Any]:
        """环境读数 + `revision`；第一次有人问的时候才起轮询线程。"""
        self._environment_monitor.start()
        return self._environment_monitor.snapshot()

    def _game_path_or_none(self) -> Path | None:
        value = effective_game_path(self.config)
        if not value:
            return None
        path = Path(value).expanduser()
        return path if (path / "Sprocket.exe").is_file() else None

    def providers_table(self) -> tuple[dict[str, Any] | None, str]:
        """加载器包↔游戏表：索引里那份优先，其次用上次同步缓存下来的那份，都没有就是「没有表」。

        本地不放内置副本：表是平台事实，会变；写死一份只会和注册表各说各话。
        """
        registry = self.service.registry if self.service is not None else None
        table = getattr(registry, "provider_table", None)
        if table and table.get("entries"):
            self._providers_table_cache = table
            return table, "registry"
        if not self._providers_table_read:
            self._providers_table_cache = read_providers_table(self.config_store.app_dir)
            self._providers_table_read = True
        if self._providers_table_cache is None:
            return None, "missing"
        return self._providers_table_cache, "cache"

    def current_environment(self) -> CapabilityEnvironment:
        """判定用的能力表：版本来自监听缓存，表来自索引（没有就用同步缓存下来的那份）。

        每个加载器包取「在用版本」：装了就是装的那版，没装就用已知的最新版。能力版本由
        加载器包的 `provides` 给出（`{version}` 按在用版本替换）—— 包版本与它提供的能力
        版本可以不同，这正是桥接加载器能供给另一个加载器能力的原因。
        """
        snapshot = self.environment_snapshot()
        sprocket = dict(snapshot.get("sprocket") or {})
        latest_loaders = dict(snapshot.get("latest_loaders") or {})
        states = dict(snapshot.get("loaders") or {})
        table, _source = self.providers_table()
        registry = self.service.registry if self.service is not None else None
        loaders: dict[str, str] = {}
        for loader_id in set(states) | set(latest_loaders):
            info = states.get(loader_id) or {}
            version = str(info.get("version") or latest_loaders.get(loader_id) or "")
            if version:
                loaders[loader_id] = version
        capabilities: dict[str, str] = {}
        recorded: set[str] = set()
        if registry is not None:
            # 先铺未安装的（用已知最新版），再让已安装的覆盖：实际装上的那个才算数。
            for installed_pass in (False, True):
                for loader_id in sorted(loaders):
                    info = states.get(loader_id) or {}
                    if bool(info.get("installed")) != installed_pass:
                        continue
                    try:
                        package = registry.get(loader_id)
                    except ModManagerError:
                        continue
                    for capability_id, spec in package.capabilities().items():
                        capabilities[capability_id] = (
                            loaders[loader_id] if spec == VERSION_TEMPLATE else spec
                        )
                        if installed_pass:
                            recorded.add(capability_id)
        # 磁盘上检测到、但没进安装记录的运行时（管理器之外装的加载器）：按「装上了」处理，
        # 版本取磁盘上的真值；已装供给者给出的值不覆盖。
        for capability_id, version in (snapshot.get("detected") or {}).items():
            capability_id = str(capability_id)
            if capability_id in recorded:
                continue
            capabilities[capability_id] = str(version)
        return CapabilityEnvironment(
            game_id=getattr(registry, "game_id", DEFAULT_GAME_CAPABILITY) if registry is not None else DEFAULT_GAME_CAPABILITY,
            sprocket=str(sprocket.get("version") or "") or None,
            sprocket_state=str(sprocket.get("state") or "unconfigured"),
            capabilities=capabilities,
            loaders=loaders,
            table=table,
        )

    def environment_payload(self) -> dict[str, Any]:
        _table, table_source = self.providers_table()
        return self.current_environment().as_dict(table_source=table_source)

    def _read_environment(self) -> dict[str, Any]:
        """本机环境：Sprocket 版本（读游戏目录）+ 每个加载器装的是哪版（安装记录 + 磁盘检测）。

        只读本地文件、不联网 —— 监听线程每隔一秒就可能走一遍这里。安装记录读一次，
        加载器状态与指纹要监听的目录都从这一份来；`detected` 一份供加载器状态与能力表共用。
        """
        game_path = self._game_path_or_none()
        if game_path is None:
            self._mod_directories = MOD_DIRECTORIES
            return {
                "sprocket": GameVersion.unconfigured().as_dict(),
                "loaders": {},
            }
        installed: dict[str, dict[str, Any]] = {}
        registry = self.service.registry if self.service is not None else None
        if registry is not None:
            installed = self.service.installed(game_path)
        detected = detected_capabilities(game_path)
        self._mod_directories = self._active_mod_directories(game_path, installed)
        return {
            "sprocket": read_game_version(game_path).as_dict(),
            "loaders": self._installed_loaders(installed, detected),
            "detected": detected,
        }

    def _active_mod_directories(
            self,
            game_path: Path,
            installed: dict[str, dict[str, Any]],
    ) -> tuple[str, ...]:
        """指纹要监听的目录：活跃标识符的目录，来自已装供给者或磁盘上检测到的运行时。"""
        registry = self.service.registry if self.service is not None else None
        if registry is None:
            return MOD_DIRECTORIES
        capabilities = getattr(self.service.environment, "capabilities", None)
        directories = mod_directory_paths(
            game_path,
            registry.packages,
            tuple(installed),
            capabilities if isinstance(capabilities, dict) else {},
        )
        return directories or MOD_DIRECTORIES

    def _installed_loaders(
            self,
            installed: dict[str, dict[str, Any]],
            detected: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        """每个加载器类包在盘上的样子；「装没装」由 `runtime_states` 一处说了算。

        这里覆盖**所有**加载器类包而不只是基础运行时：桥接/翻译/补丁加载器装上也提供能力
        （`provides`），能力表少了它就判不了依赖那项能力的模组。
        """
        registry = self.service.registry if self.service is not None else None
        if registry is None:
            return {}
        loaders = tuple(package for package in registry.packages if package.is_loader)
        states = runtime_states(loaders, installed, detected)
        return {
            package.id: {
                "installed": states[package.id][0],
                "version": states[package.id][1] or None,
            }
            for package in loaders
        }

    def __getattr__(self, name: str) -> Any:
        for controller in self.__dict__.get("_controllers", ()):
            descriptor = vars(type(controller)).get(name)
            if descriptor is not None:
                return descriptor.__get__(controller, type(controller))
        raise AttributeError(name)

    def _configure_service_network(self, service: ModManagerService) -> None:
        http = getattr(service, "http", None)
        configure = getattr(http, "configure_network", None)
        if configure is not None:
            configure(
                effective_proxy_url(self.config),
                effective_github_proxy_url(self.config),
            )

    def bind_window(self, window: Any) -> None:
        self._window = window

    @property
    def language(self) -> str:
        configured = str(self.config.get("language", "auto"))
        return detect_language() if configured == "auto" else configured

    @staticmethod
    def _success(**payload: Any) -> dict[str, Any]:
        return {"ok": True, **payload}

    @staticmethod
    def _failure(exc: Exception, *, code: str = "operation_failed") -> dict[str, Any]:
        LOGGER.warning("API operation failed code=%s error=%s", code, exc)
        return {"ok": False, "code": code, "message": str(exc)}

    # Public WebView methods remain explicit; implementations live in feature controllers.
    def bootstrap(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._settings_controller.bootstrap(*args, **kwargs)

    def startup_trace(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._settings_controller.startup_trace(*args, **kwargs)

    def client_log(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._settings_controller.client_log(*args, **kwargs)

    def get_settings(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._settings_controller.get_settings(*args, **kwargs)

    def find_game_path(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._settings_controller.find_game_path(*args, **kwargs)

    def choose_game_path(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._settings_controller.choose_game_path(*args, **kwargs)

    def save_settings(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._settings_controller.save_settings(*args, **kwargs)

    def set_demo_github_login(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.set_demo_github_login(*args, **kwargs)

    def start_github_device_login(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.start_github_device_login(*args, **kwargs)

    def poll_github_device_login(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.poll_github_device_login(*args, **kwargs)

    def cancel_github_device_login(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.cancel_github_device_login(*args, **kwargs)

    def sync_github_gist(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.sync_github_gist(*args, **kwargs)

    def resolve_github_gist_conflicts(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.resolve_github_gist_conflicts(*args, **kwargs)

    def get_developer_servers(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.get_developer_servers(*args, **kwargs)

    def add_developer_server(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.add_developer_server(*args, **kwargs)

    def activate_developer_server(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.activate_developer_server(*args, **kwargs)

    def remove_developer_server(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.remove_developer_server(*args, **kwargs)

    def logout_github(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._private_controller.logout_github(*args, **kwargs)

    def load_catalog(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.load_catalog(*args, **kwargs)

    def get_installed(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.get_installed(*args, **kwargs)

    def get_local_mods(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.get_local_mods(*args, **kwargs)

    def adopt_existing(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.adopt_existing(*args, **kwargs)

    def verify_installed(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.verify_installed(*args, **kwargs)

    def set_integrity_suppressed(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.set_integrity_suppressed(*args, **kwargs)

    def toggle_mod(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.toggle_mod(*args, **kwargs)

    def open_mod_location(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.open_mod_location(*args, **kwargs)

    def get_package_readme(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.get_package_readme(*args, **kwargs)

    def open_readme_link(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.open_readme_link(*args, **kwargs)

    def get_environment(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.get_environment(*args, **kwargs)

    def get_modloaders(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.get_modloaders(*args, **kwargs)

    def install_modloader(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.install_modloader(*args, **kwargs)

    def remove_modloader(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.remove_modloader(*args, **kwargs)

    def plan_install(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.plan_install(*args, **kwargs)

    def enqueue_install(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.enqueue_install(*args, **kwargs)

    def update_all(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.update_all(*args, **kwargs)

    def remove(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.remove(*args, **kwargs)

    def kill_sprocket(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.kill_sprocket(*args, **kwargs)

    def get_queue(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.get_queue(*args, **kwargs)

    def cancel_queue_item(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.cancel_queue_item(*args, **kwargs)

    def clear_finished(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.clear_finished(*args, **kwargs)

    def _log_source_entries(self) -> list[dict[str, Any]]:
        """可上传的日志：管理器那份永远在，运行时那份来自活跃标识符。

        `path` 是给界面看的（管理器日志是绝对路径，运行时日志相对游戏根目录），`target` 是
        真正要读的文件 —— 运行时的相对路径配上游戏根目录才读得到。
        """
        manager = manager_log_path(self.config_store.app_dir)
        entries: list[dict[str, Any]] = [{
            "id": "manager",
            "kind": "manager",
            "loader": "",
            "path": str(manager),
            "target": manager,
            "available": True,
        }]
        game_path = self._game_path_or_none()
        if game_path is None:
            return entries
        registry = self.service.registry if self.service is not None else None
        packages = registry.packages if registry is not None else ()
        installed = self.service.installed(game_path) if registry is not None else {}
        capabilities = getattr(self.service.environment, "capabilities", None)
        for source in log_sources(
                game_path,
                packages,
                tuple(installed),
                capabilities if isinstance(capabilities, dict) else {},
        ):
            target = game_path / source.path
            entries.append({
                "id": source.id,
                "kind": "loader",
                "loader": source.loader,
                "path": source.path,
                "target": target,
                "available": target.is_file(),
            })
        return entries

    def get_log_sources(self) -> dict[str, Any]:
        try:
            sources = [
                {key: value for key, value in entry.items() if key != "target"}
                for entry in self._log_source_entries()
            ]
            return self._success(sources=sources)
        except (OSError, ValueError, ModManagerError) as exc:
            return self._failure(exc, code="log_sources_failed")

    def upload_log(self, source_id: str = "") -> dict[str, Any]:
        try:
            entry = next(
                (item for item in self._log_source_entries() if item["id"] == str(source_id)),
                None,
            )
            if entry is None:
                return self._failure(
                    ValueError(f"unknown log source: {source_id}"),
                    code="log_source_unknown",
                )
            if not entry["available"]:
                return self._failure(
                    FileNotFoundError(f"log source is not on disk: {entry['path']}"),
                    code="log_source_unavailable",
                )
            _flush_logs()
            result = upload_log_file(
                entry["target"],
                LOG_UPLOAD_ENDPOINT,
                app_version=self.version,
            )
            LOGGER.info(
                "log uploaded source_id=%s request_id=%s bytes=%s",
                entry["id"],
                result.request_id,
                result.bytes_uploaded,
            )
            return self._success(
                request_id=result.request_id,
                status=result.status,
                bytes_uploaded=result.bytes_uploaded,
                url=result.url,
            )
        except (OSError, ValueError, ModManagerError) as exc:
            return self._failure(exc, code="log_upload_failed")

    def open_manager_directory(self) -> dict[str, Any]:
        try:
            open_directory(self.config_store.app_dir)
            LOGGER.info("opened manager data directory path=%s", self.config_store.app_dir)
            return self._success(path=str(self.config_store.app_dir))
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="open_manager_directory_failed")

    def get_manager_update(self) -> dict[str, Any]:
        """管理器自己的版本：最新发布是什么、有没有新、这台机器能不能就地换掉自己。"""
        try:
            release = self._current_service().github.latest_repository_release(MANAGER_REPOSITORY)
            current = Version.parse(self.version)
            update = update_from_release(release, self.version)
            return self._success(
                current=self.version,
                latest=str(release.version),
                newer=release.version > current,
                page_url=release.page_url,
                notes=str(release.notes or ""),
                can_self_update=can_self_update(),
                size=int(update.size) if update else 0,
            )
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="update_check_failed")

    def apply_manager_update(self) -> dict[str, Any]:
        """下载新版并交给换壳子进程，随后关窗口退出，让新版本替换掉正在运行的自己。"""
        try:
            current = frozen_executable()
            if current is None:
                return self._failure(
                    RuntimeError("self-update needs the packaged single-file build"),
                    code="self_update_unavailable",
                )
            service = self._current_service()
            update = update_from_release(
                service.github.latest_repository_release(MANAGER_REPOSITORY), self.version
            )
            if update is None:
                return self._failure(
                    RuntimeError("no newer release to install"), code="update_not_available"
                )
            staged = staged_executable(current)
            download_update(service.http, update, staged)
            launch_self_update(current, staged, app_dir=self.config_store.app_dir)
            self._exit_for_self_update()
            return self._success(version=update.version, staged=str(staged))
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="update_apply_failed")

    def _exit_for_self_update(self, delay: float = 1.0) -> None:
        """先让「已开始更新」回到界面，再关窗口退出：自身文件要等进程结束才解锁。"""
        with self._state_lock:
            if self._destroy_scheduled:
                return
            self._destroy_scheduled = True
        self._close_pending = True

        def finish() -> None:
            time.sleep(delay)
            self._environment_monitor.stop()
            self.install_queue.close(timeout=5)
            window = self._window
            if window is not None:
                try:
                    window.destroy()
                except Exception:  # noqa: BLE001 - 关不掉也要退出，锁必须放开
                    LOGGER.exception("could not close the window before self-update")
            time.sleep(0.5)
            _flush_logs()
            os._exit(0)

        threading.Thread(target=finish, name="sprocket-self-update", daemon=True).start()

    def open_url(self, url: str) -> dict[str, Any]:
        try:
            parsed = urlparse(str(url))
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("only HTTPS links can be opened")
            host = parsed.hostname.casefold()
            if host not in {"github.com", "sprocketmods.furryaxw.top"}:
                raise ValueError("link host is not allowed")
            if host == "github.com":
                if parsed.path.rstrip("/").casefold() == "/login/device":
                    webbrowser.open(str(url))
                    return self._success()
                allowed_repositories = {MANAGER_REPOSITORY.casefold()}
                service = self._current_service()
                if service.registry:
                    allowed_repositories.update(
                        package.repository.casefold()
                        for package in service.registry.packages
                    )
                path = parsed.path.strip("/").casefold()
                if not any(
                        path == repository or path.startswith(repository + "/")
                        for repository in allowed_repositories
                ):
                    raise ValueError("GitHub link is outside the loaded Registry")
            webbrowser.open(str(url))
            return self._success()
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="open_url_failed")

    def on_closing(self) -> bool | None:
        LOGGER.info("window closing requested")
        self._close_pending = True
        queue_closed = self.install_queue.close(timeout=0.25)
        if queue_closed and self._loader_idle.wait(0.25):
            mutation_finished = self._mutation_lock.acquire(blocking=False)
            if mutation_finished:
                self._mutation_lock.release()
                return None
        with self._state_lock:
            if self._destroy_scheduled:
                return False
            self._destroy_scheduled = True

        def finish_close() -> None:
            LOGGER.debug("waiting for background mutations before close")
            self.install_queue.close(timeout=None)
            self._loader_idle.wait()
            with self._mutation_lock:
                pass
            if self._window is not None:
                self._window.destroy()

        threading.Thread(target=finish_close, name="sprocket-close", daemon=True).start()
        return False

    def on_closed(self) -> None:
        LOGGER.info("window closed; draining background work")
        self._environment_monitor.stop()
        self.install_queue.close(timeout=5)
        self._loader_idle.wait(5)
        if self._mutation_lock.acquire(timeout=5):
            self._mutation_lock.release()
