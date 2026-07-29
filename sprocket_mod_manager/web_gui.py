from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import urlparse

from .catalog import load_catalog
from .config import (
    ConfigStore,
    detect_game_path,
    detect_language,
    effective_game_path,
    effective_index_url,
)
from .errors import ModManagerError
from .install_queue import ACTIVE_STATES, InstallQueue, InstallQueueEntry
from .models import RegistryPackage, ReleaseInfo, ResolutionPlan
from .semver import Version
from .service import DEFAULT_INDEX_URL, ModManagerService


MANAGER_REPOSITORY = "furryaxw/sprocket-mods"
MANAGER_REPOSITORY_URL = f"https://github.com/{MANAGER_REPOSITORY}"
REGISTRY_WEBSITE_URL = "https://sprocketmods.furryaxw.top"


class GamePathRequiredError(ValueError):
    pass


def _ui_directory() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    bundled = bundle_root / "sprocket_mod_manager" / "client_ui"
    if bundled.is_dir():
        return bundled
    return Path(__file__).resolve().with_name("client_ui")


def _source_from_config(config: dict[str, Any]) -> str | Path:
    source = effective_index_url(config)
    path = Path(source).expanduser()
    return path if path.is_file() else source


def _release_data(release: ReleaseInfo | None) -> dict[str, Any] | None:
    if release is None:
        return None
    return {
        "tag": release.tag,
        "version": str(release.version),
        "published_at": release.published_at,
        "page_url": release.page_url,
        "assets": [
            {"name": asset.name, "size": asset.size}
            for asset in release.assets
        ],
    }


class ClientApi:
    def __init__(
        self,
        version: str,
        *,
        app_dir: Path | None = None,
        service_factory: Callable[[Path], ModManagerService] = ModManagerService,
    ) -> None:
        self.version = version
        self.config_store = ConfigStore(app_dir)
        self.config = self.config_store.load()
        self._service_factory = service_factory
        self.service = service_factory(self.config_store.app_dir)
        self.latest: dict[str, ReleaseInfo | None] = {}
        self._catalog_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._window: Any = None
        self._close_pending = False
        self._destroy_scheduled = False
        self.install_queue = InstallQueue(self._run_queued_install)

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
        return {"ok": False, "code": code, "message": str(exc)}

    def bootstrap(self) -> dict[str, Any]:
        self.config = self.config_store.load()
        return self._success(
            version=self.version,
            language=self.language,
            settings=self._settings_data(),
            links={
                "repository": MANAGER_REPOSITORY_URL,
                "registry": REGISTRY_WEBSITE_URL,
            },
        )

    def _settings_data(self) -> dict[str, Any]:
        return {
            "language": str(self.config.get("language", "auto")),
            "game_path": str(self.config.get("game_path", "") or ""),
            "index_url": str(self.config.get("index_url", "") or ""),
            "index_placeholder": DEFAULT_INDEX_URL,
            "game_path_placeholder": "",
        }

    def get_settings(self) -> dict[str, Any]:
        self.config = self.config_store.load()
        return self._success(settings=self._settings_data(), language=self.language)

    def find_game_path(self) -> dict[str, Any]:
        try:
            return self._success(path=detect_game_path())
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="game_path_detection_failed")

    def choose_game_path(self) -> dict[str, Any]:
        if self._window is None:
            return self._failure(RuntimeError("window is not ready"))
        try:
            import webview

            current = effective_game_path(self.config)
            result = self._window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=current or "",
                allow_multiple=False,
            )
            selected = result[0] if result else ""
            return self._success(path=selected)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc, code="file_dialog_failed")

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        try:
            language = str(values.get("language", "auto"))
            if language not in {"auto", "zh", "en"}:
                raise ValueError("unsupported interface language")
            self.config = {
                "language": language,
                "game_path": str(values.get("game_path", "")).strip(),
                "index_url": str(values.get("index_url", "")).strip(),
            }
            self.config_store.save(self.config)
            return self._success(settings=self._settings_data(), language=self.language)
        except (OSError, ValueError) as exc:
            return self._failure(exc, code="settings_save_failed")

    def load_catalog(self, refresh: bool = False) -> dict[str, Any]:
        if not self._catalog_lock.acquire(blocking=False):
            return self._failure(RuntimeError("catalog load is already running"), code="catalog_busy")
        try:
            self.config = self.config_store.load()
            service = self._service_factory(self.config_store.app_dir)
            service, latest = load_catalog(
                service,
                _source_from_config(self.config),
                refresh=bool(refresh),
            )
            with self._state_lock:
                self.service = service
                self.latest = latest
            return self._success(
                packages=self._catalog_data(service, latest),
                installed=self._installed_data(service),
                source=effective_index_url(self.config),
            )
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="catalog_load_failed")
        finally:
            self._catalog_lock.release()

    def _catalog_data(
        self,
        service: ModManagerService,
        latest: dict[str, ReleaseInfo | None],
    ) -> list[dict[str, Any]]:
        registry = service.registry
        if registry is None:
            return []
        installed = self._installed(service)
        packages: list[dict[str, Any]] = []
        for package in registry.packages:
            release = latest.get(package.id)
            selected_assets = (
                service.github.install_assets(package, release)
                if release is not None
                else ()
            )
            packages.append(
                {
                    "id": package.id,
                    "name": package.name,
                    "display_name": dict(package.display_name),
                    "description": dict(package.description),
                    "authors": list(package.authors),
                    "repository": package.repository,
                    "repository_url": f"https://github.com/{package.repository}",
                    "license": package.license,
                    "category": package.category,
                    "tags": list(package.tags),
                    "dependencies": [dict(item) for item in package.dependencies],
                    "release": _release_data(release),
                    "install_assets": [asset.name for asset in selected_assets],
                    "installed": self._installed_entry(installed.get(package.id)),
                }
            )
        return packages

    @staticmethod
    def _installed_entry(info: dict[str, Any] | None) -> dict[str, Any] | None:
        if not info:
            return None
        return {
            "name": str(info.get("name", "")),
            "version": str(info.get("version", "")),
            "requested": bool(info.get("requested")),
            "dependencies": list(info.get("dependencies", ())),
        }

    def _installed(self, service: ModManagerService | None = None) -> dict[str, dict[str, Any]]:
        value = effective_game_path(self.config)
        if not value:
            return {}
        path = Path(value).expanduser()
        if not (path / "Sprocket.exe").is_file():
            return {}
        return (service or self.service).installed(path)

    def _current_service(self) -> ModManagerService:
        with self._state_lock:
            return self.service

    def _installed_data(self, service: ModManagerService | None = None) -> list[dict[str, Any]]:
        installed = self._installed(service)
        return [
            {"id": package_id, **(self._installed_entry(info) or {})}
            for package_id, info in sorted(installed.items())
        ]

    def get_installed(self) -> dict[str, Any]:
        try:
            return self._success(installed=self._installed_data(self._current_service()))
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="installed_load_failed")

    def _valid_game_path(self) -> Path:
        value = effective_game_path(self.config)
        path = Path(value).expanduser() if value else None
        if path is None or not (path / "Sprocket.exe").is_file():
            raise GamePathRequiredError("valid Sprocket game path is required")
        return path

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

    def plan_install(self, package_ids: list[str]) -> dict[str, Any]:
        try:
            game_path = self._valid_game_path()
            service = self._current_service()
            installed = service.installed(game_path)
            plans: list[dict[str, Any]] = []
            skipped: list[str] = []
            for package_id in dict.fromkeys(str(item) for item in package_ids):
                package = self._package(service, package_id)
                plan = service.resolve(package.id)
                root = plan.by_id()[package.id]
                if installed.get(package.id, {}).get("version") == str(root.release.version):
                    skipped.append(package.id)
                    continue
                plans.append(self._plan_data(service, package, plan))
            return self._success(plans=plans, skipped=skipped)
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "install_plan_failed"
            )
            return self._failure(exc, code=code)

    def enqueue_install(self, package_ids: list[str]) -> dict[str, Any]:
        try:
            game_path = self._valid_game_path()
            service = self._current_service()
            installed = service.installed(game_path)
            eligible: list[str] = []
            for package_id in dict.fromkeys(str(item) for item in package_ids):
                package = self._package(service, package_id)
                plan = service.resolve(package.id)
                root = plan.by_id()[package.id]
                if installed.get(package.id, {}).get("version") != str(root.release.version):
                    eligible.append(package.id)
            added = self.install_queue.enqueue(eligible, game_path, context=service)
            return self._success(added=[entry.task_id for entry in added], count=len(added))
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
            service = self._current_service()
            installed = service.installed(game_path)
            updates: list[str] = []
            for package_id, info in installed.items():
                if not info.get("requested"):
                    continue
                plan = service.resolve(package_id)
                latest = plan.by_id()[package_id].release.version
                if info.get("version") != str(latest):
                    updates.append(package_id)
            added = self.install_queue.enqueue(updates, game_path, context=service)
            return self._success(added=[entry.task_id for entry in added], count=len(added))
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            code = (
                "game_path_required"
                if isinstance(exc, GamePathRequiredError)
                else "update_failed"
            )
            return self._failure(exc, code=code)

    def remove(self, package_id: str) -> dict[str, Any]:
        try:
            if any(entry.state in ACTIVE_STATES for entry in self.install_queue.snapshot()):
                raise RuntimeError("wait for the install queue to finish before removing packages")
            game_path = self._valid_game_path()
            service = self._current_service()
            package = self._package(service, str(package_id))
            removed, warnings = service.remove(package.id, game_path)
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
        service.install(entry.package_id, entry.game_path, progress=progress)

    def _queue_data(self) -> list[dict[str, Any]]:
        return [
            {
                "task_id": entry.task_id,
                "package_id": entry.package_id,
                "state": entry.state,
                "message": entry.message,
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
        self._close_pending = True
        if self.install_queue.close(timeout=0.25):
            return None
        with self._state_lock:
            if self._destroy_scheduled:
                return False
            self._destroy_scheduled = True

        def finish_close() -> None:
            self.install_queue.close(timeout=None)
            if self._window is not None:
                self._window.destroy()

        threading.Thread(target=finish_close, name="sprocket-close", daemon=True).start()
        return False

    def on_closed(self) -> None:
        self.install_queue.close(timeout=5)


def run_gui(version: str) -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("pywebview is required for the desktop client") from exc

    ui_dir = _ui_directory()
    index_path = ui_dir / "index.html"
    if not index_path.is_file():
        raise RuntimeError(f"client UI is missing: {index_path}")

    api = ClientApi(version)
    window = webview.create_window(
        "Sprocket Mod Manager",
        url=index_path.resolve().as_uri(),
        js_api=api,
        width=1240,
        height=780,
        min_size=(960, 640),
        background_color="#101213",
        text_select=True,
    )
    if window is None:
        raise RuntimeError("failed to create the client window")
    api.bind_window(window)
    window.events.closing += api.on_closing
    window.events.closed += api.on_closed
    webview.start(
        gui="edgechromium",
        debug=False,
        private_mode=False,
        storage_path=str(api.config_store.app_dir / "webview"),
    )
