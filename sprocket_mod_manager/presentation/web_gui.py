from __future__ import annotations

import hashlib
import logging
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .api_constants import MANAGER_REPOSITORY
from .controllers import CatalogController, InstallationController, PrivateDistributionController, SettingsController
from ..application.install_queue import InstallQueue
from ..application.service import ModManagerService
from ..domain.compatibility import Environment
from ..domain.errors import ModManagerError
from ..domain.models import ReleaseInfo
from ..domain.semver import Version
from ..infrastructure.config import ConfigStore, detect_language, effective_game_path, effective_github_proxy_url, \
    effective_proxy_url
from ..infrastructure.credential_store import CredentialStore
from ..infrastructure.desktop import open_directory
from ..infrastructure.app_logging import manager_log_path
from ..infrastructure.environment_cache import read_environment_table
from ..infrastructure.environment_monitor import EnvironmentMonitor, game_environment_fingerprint
from ..infrastructure.game_version import GameVersion, read_game_version
from ..infrastructure.log_upload import upload_latest_log, upload_log_file
from ..infrastructure.melonloader import MELONLOADER_REPOSITORY, MelonLoaderManager
from ..infrastructure.private_servers import PrivateCatalogCache

LOGGER = logging.getLogger(__name__)
LOG_UPLOAD_ENDPOINT = "https://paste.furryaxw.top/api/q/"


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
        self._melonloader_idle = threading.Event()
        self._melonloader_idle.set()
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
        http = getattr(self.service, "http", None)
        if http is None:
            http = ModManagerService(self.config_store.app_dir).http
        self.melonloader = MelonLoaderManager(self.config_store.app_dir, http)
        self._environment_monitor = EnvironmentMonitor(
            self._read_environment,
            lambda: game_environment_fingerprint(self._game_path_or_none()),
        )
        # 环境表（加载器↔游戏）：内存里留一份，省掉每秒问一次时读盘。
        self._environment_table_cache: dict[str, Any] | None = None
        self._environment_table_read = False
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

    def environment_table(self) -> tuple[dict[str, Any] | None, str]:
        """加载器↔游戏表：索引里那份优先，其次用上次同步缓存下来的那份，都没有就是「没有表」。

        本地不放内置副本：表是平台事实，会变；写死一份只会和注册表各说各话。
        """
        registry = self.service.registry if self.service is not None else None
        environment = getattr(registry, "environment", None)
        if environment and environment.get("entries"):
            self._environment_table_cache = environment
            return environment, "registry"
        if not self._environment_table_read:
            self._environment_table_cache = read_environment_table(self.config_store.app_dir)
            self._environment_table_read = True
        if self._environment_table_cache is None:
            return None, "missing"
        return self._environment_table_cache, "cache"

    def current_environment(self) -> Environment:
        """判定用的环境：版本来自监听缓存，表来自索引（没有就用同步缓存下来的那份）。"""
        snapshot = self.environment_snapshot()
        sprocket = dict(snapshot.get("sprocket") or {})
        melonloader = dict(snapshot.get("melonloader") or {})
        table, _source = self.environment_table()
        return Environment(
            sprocket=str(sprocket.get("version") or "") or None,
            sprocket_state=str(sprocket.get("state") or "unconfigured"),
            melonloader=melonloader.get("version") or snapshot.get("latest_loader"),
            table=table,
        )

    def environment_payload(self) -> dict[str, Any]:
        _table, table_source = self.environment_table()
        return self.current_environment().as_dict(table_source=table_source)

    def _read_environment(self) -> dict[str, Any]:
        """本机环境：Sprocket 版本（读游戏目录）+ MelonLoader 版本（本机检测）。

        只读本地文件、不联网 —— 监听线程每隔一秒就可能走一遍这里。
        """
        game_path = self._game_path_or_none()
        if game_path is None:
            return {
                "sprocket": GameVersion.unconfigured().as_dict(),
                "melonloader": {"installed": False, "version": None},
            }
        installation = self.melonloader.detect(game_path)
        return {
            "sprocket": read_game_version(game_path).as_dict(),
            "melonloader": {
                "installed": bool(installation.installed),
                "version": str(installation.version) if installation.version else None,
            },
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

    def get_package_readme(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.get_package_readme(*args, **kwargs)

    def open_readme_link(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.open_readme_link(*args, **kwargs)

    def get_environment(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.get_environment(*args, **kwargs)

    def get_melonloader_status(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.get_melonloader_status(*args, **kwargs)

    def install_melonloader(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.install_melonloader(*args, **kwargs)

    def plan_install(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.plan_install(*args, **kwargs)

    def enqueue_install(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.enqueue_install(*args, **kwargs)

    def update_all(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.update_all(*args, **kwargs)

    def remove(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.remove(*args, **kwargs)

    def get_queue(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.get_queue(*args, **kwargs)

    def cancel_queue_item(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.cancel_queue_item(*args, **kwargs)

    def clear_finished(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._installation_controller.clear_finished(*args, **kwargs)

    def upload_latest_log(self) -> dict[str, Any]:
        try:
            LOGGER.info("uploading MelonLoader Latest.log")
            result = upload_latest_log(
                Path(effective_game_path(self.config)),
                LOG_UPLOAD_ENDPOINT,
                app_version=self.version,
            )
            return self._success(request_id=result.request_id, status=result.status,
                                 bytes_uploaded=result.bytes_uploaded, url=result.url)
        except (OSError, ValueError, ModManagerError) as exc:
            return self._failure(exc, code="log_upload_failed")

    def open_manager_directory(self) -> dict[str, Any]:
        try:
            open_directory(self.config_store.app_dir)
            LOGGER.info("opened manager data directory path=%s", self.config_store.app_dir)
            return self._success(path=str(self.config_store.app_dir))
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="open_manager_directory_failed")

    def upload_manager_log(self) -> dict[str, Any]:
        try:
            LOGGER.info("uploading manager log")
            for handler in logging.getLogger().handlers:
                handler.flush()
            result = upload_log_file(
                manager_log_path(self.config_store.app_dir),
                LOG_UPLOAD_ENDPOINT,
                app_version=self.version,
            )
            LOGGER.info(
                "manager log uploaded request_id=%s bytes=%s",
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
            return self._failure(exc, code="manager_log_upload_failed")

    def get_manager_update(self) -> dict[str, Any]:
        try:
            release = self._current_service().github.latest_repository_release(MANAGER_REPOSITORY)
            current = Version.parse(self.version)
            return self._success(
                current=self.version,
                latest=str(release.version),
                newer=release.version > current,
                page_url=release.page_url,
            )
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="update_check_failed")

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
                allowed_repositories = {
                    MANAGER_REPOSITORY.casefold(),
                    MELONLOADER_REPOSITORY.casefold(),
                }
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
        if queue_closed and self._melonloader_idle.wait(0.25):
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
            self._melonloader_idle.wait()
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
        self._melonloader_idle.wait(5)
        if self._mutation_lock.acquire(timeout=5):
            self._mutation_lock.release()
