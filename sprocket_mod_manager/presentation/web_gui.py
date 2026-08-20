from __future__ import annotations

import hashlib
import logging
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .api_constants import MANAGER_REPOSITORY
from .api_support import GamePathRequiredError
from .controllers import CatalogController, InstallationController, PrivateDistributionController, SettingsController
from ..application.install_queue import InstallQueue
from ..application.service import ModManagerService
from ..domain.errors import ModManagerError
from ..domain.models import ReleaseInfo
from ..domain.semver import Version
from ..infrastructure.app_logging import manager_log_path
from ..infrastructure.config import ConfigStore, detect_language, effective_game_path, effective_github_proxy_url, \
    effective_proxy_url
from ..infrastructure.credential_store import CredentialStore
from ..infrastructure.desktop import open_directory
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
        self._startup_log_path = manager_log_path(self.config_store.app_dir)
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
        LOGGER.info("Client API initialized version=%s", self.version)

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

    def get_package_readme(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.get_package_readme(*args, **kwargs)

    def open_readme_link(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._catalog_controller.open_readme_link(*args, **kwargs)

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
        self.install_queue.close(timeout=5)
        self._melonloader_idle.wait(5)
        if self._mutation_lock.acquire(timeout=5):
            self._mutation_lock.release()


def run_gui(version: str, *, debug: bool = False, debug_override: bool = False) -> None:
    """Compatibility entry point; desktop hosting lives in webview_app."""
    from .webview_app import run_gui as run_webview_app

    run_webview_app(version, debug=debug, debug_override=debug_override)
