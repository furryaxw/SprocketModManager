from __future__ import annotations

import logging
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .base import ApiController
from ..api_support import release_data as _release_data, source_from_config as _source_from_config
from ...application.catalog import load_catalog
from ...application.identifiers import scan_targets, toggle_directories
from ...application.integrity import suppression_key, suppression_key_of, suppression_keys
from ...application.local_mods import scan_local_mods, summarize
from ...application.service import ModManagerService
from ...domain.compatibility import CapabilityEnvironment, release_verdict
from ...domain.errors import ModManagerError, ModToggleError
from ...domain.models import RegistryPackage, ReleaseInfo
from ...infrastructure.config import effective_game_path, effective_index_url
from ...infrastructure.desktop import reveal_in_file_manager
from ...infrastructure.dll_metadata import MELON_KIND_MODS, flush_metadata_cache
from ...infrastructure.suppression_store import store_for
from ...infrastructure.mod_toggle import (
    canonical_relative,
    direct_files,
    is_disabled_path,
    is_in_roots,
    is_loadable_path,
    apply_enabled,
    resolve_mod_path,
)
from ...utilities.package_paths import validate_relative_path

LOGGER = logging.getLogger(__name__)


class CatalogController(ApiController):
    def load_catalog(self, refresh: bool = False) -> dict[str, Any]:
        if not self._catalog_lock.acquire(blocking=False):
            return self._failure(RuntimeError("catalog load is already running"), code="catalog_busy")
        try:
            self.config = self.config_store.load()
            service = self._service_factory(self.config_store.app_dir)
            self._configure_service_network(service)
            service.environment = self.current_environment()
            service, latest = load_catalog(
                service,
                _source_from_config(self.config),
                refresh=bool(refresh),
            )
            adopted = self._adopt_existing(service)
            with self._state_lock:
                self.service = service
                self.latest = latest
            # 加载器清单来自注册表：注册表刚换过，环境读数必须重来一次，否则缓存里那份
            # 还是「没有注册表」时读出来的空清单。
            self._environment_monitor.invalidate()
            snapshot = self._snapshot(service)
            return self._success(
                packages=self._catalog_data(service, latest, installed=snapshot["installed"]),
                installed=self._installed_data(service, installed=snapshot["installed"]),
                unrecognized=self._unrecognized_mods(service, local=snapshot["local"]),
                local_mods=snapshot["local"]["mods"],
                local_summary=snapshot["local"]["summary"],
                has_any_mods=self._has_any_mods(snapshot["local"]),
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
            *,
            installed: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        registry = service.registry
        if registry is None:
            return []
        environment = self._environment()
        records = self._installed(service) if installed is None else installed
        packages: list[dict[str, Any]] = []
        for package in registry.packages:
            release = latest.get(package.id)
            selected_assets = (
                service.github.install_assets(package, release)
                if release is not None
                else ()
            )
            release_data = _release_data(release)
            if release_data is not None:
                release_data["verdict"] = release_verdict(
                    environment, category=package.category, dependencies=release.dependencies
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
                    "kind": package.kind,
                    "category": package.category,
                    "tags": list(package.tags),
                    "dependencies": [dict(item) for item in package.dependencies],
                    "recommendations": list(package.recommendations),
                    "featured": package.featured,
                    "release": release_data,
                    "releases": self._release_verdicts(service, package, environment),
                    "install_assets": [asset.name for asset in selected_assets],
                    "installed": self._installed_entry(records.get(package.id)),
                }
            )
        return packages

    @staticmethod
    def _release_verdicts(
            service: ModManagerService,
            package: RegistryPackage,
            environment: CapabilityEnvironment,
    ) -> list[dict[str, Any]]:
        """该包每个可安装版本的三色判定（新到旧）：界面拿它决定隐藏、颜色和默认选中。"""
        if package.releases is not None:
            releases = package.releases
        else:
            try:
                releases = service.github.releases(package)
            except (ModManagerError, OSError, ValueError):
                return []
        return [
            {
                "tag": release.tag,
                "version": str(release.version),
                "verdict": release_verdict(
                    environment, category=package.category, dependencies=release.dependencies
                ),
                "compatibility": dict(release.compatibility) if release.compatibility else None,
                # 声明的原始区间与逐轴结果：详情页照着摆，不再自己解析一遍。
                "dependencies": [dict(item) for item in release.dependencies],
                "axes": environment.axes(release.dependencies),
            }
            for release in releases
            if service.github.install_assets(package, release)
        ]

    def _environment(self) -> CapabilityEnvironment:
        """判定用的环境：与左下角显示的是同一份（同一个监听缓存）。"""
        return self.current_environment()

    @staticmethod
    def _installed_entry(info: dict[str, Any] | None) -> dict[str, Any] | None:
        if not info:
            return None
        return {
            "name": str(info.get("name", "")),
            "version": str(info.get("version", "")),
            "requested": bool(info.get("requested")),
            "dependencies": list(info.get("dependencies", ())),
            # `corrupted` 每次刷新现场算出来（磁盘 hash vs 发布版本 hash）；
            # `integrity` 给出更细的状态（release/local/suppressed/...）。
            "corrupted": bool(info.get("corrupted")),
            "integrity": str(info.get("integrity", "")),
            "suppressed": bool(info.get("suppressed")),
            # 受管文件（规范相对路径）。界面用它来"抑制某个文件的损坏提示"——
            # 抑制是 per-file 的，没有路径就只能看不能操作。
            "files": [str(item) for item in (info.get("files") or ()) if isinstance(item, str)],
        }

    def _suppressed_paths(self) -> list[str]:
        """用户显式抑制损坏提示的条目：存在**游戏目录**的 `SprocketModManager/suppression.json`。

        条目两种形式：`<package id>:<文件名>`（已归属的文件）与规范相对路径（无归属的本地文件）。
        """
        game_path = self._resolved_game_path_or_none()
        if game_path is None:
            return []
        return store_for(game_path).load()

    def _prune_suppressions(self, service: ModManagerService) -> None:
        """把**已失效**的抑制条目从名单里删掉（包记录没了、路径也没了；见 `integrity.suppression_report`）。"""
        stale = set(service.stale_suppressions()) if hasattr(service, "stale_suppressions") else set()
        if not stale:
            return
        game_path = self._resolved_game_path_or_none()
        if game_path is None:
            return
        store = store_for(game_path)
        entries = store.load()
        kept = [item for item in entries if item.strip() not in stale]
        if len(kept) == len(entries):
            return
        store.save(kept)
        LOGGER.info("dropped %d stale corruption suppression(s)", len(entries) - len(kept))

    def _installed(self, service: ModManagerService | None = None) -> dict[str, dict[str, Any]]:
        value = effective_game_path(self.config)
        if not value:
            return {}
        path = Path(value).expanduser()
        if not (path / "Sprocket.exe").is_file():
            return {}
        target = service or self.service
        records = target.installed(path, suppressed=self._suppressed_paths())
        self._prune_suppressions(target)
        return records

    def _current_service(self) -> ModManagerService:
        with self._state_lock:
            service = self.service
        # 求解也要按环境筛：每次交出去之前刷新一次（读数来自监听缓存，不额外读盘）。
        service.environment = self.current_environment()
        return service

    def _installed_data(
            self,
            service: ModManagerService | None = None,
            *,
            installed: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        records = self._installed(service) if installed is None else installed
        return [
            {"id": package_id, **(self._installed_entry(info) or {})}
            for package_id, info in sorted(records.items())
        ]

    def _snapshot(self, service: ModManagerService) -> dict[str, Any]:
        """一次刷新只读一次安装记录、只扫一遍磁盘。

        列表 / 摘要 / 未识别 / 安装记录都从这份快照取。重复扫描会让每次刷新按磁盘上 DLL
        的数量多请求一遍元数据：目录遍历、元数据查表与依赖图都白做一半。
        """
        records = self._installed(service)
        local = self._local_mods_payload(service, installed=records)
        return {"installed": records, "local": local}

    def get_installed(self) -> dict[str, Any]:
        """「已安装」页的数据：**只读磁盘 + 已缓存的 Registry**，不做任何网络访问。

        认领（adoption）会去拉 GitHub Release，所以它是独立端点 `adopt_existing`，
        由前端在页面渲染完成之后再异步调用——网络永远不阻塞列表。
        """
        try:
            service = self._current_service()
            snapshot = self._snapshot(service)
            return self._success(
                installed=self._installed_data(service, installed=snapshot["installed"]),
                unrecognized=self._unrecognized_mods(service, local=snapshot["local"]),
                local_mods=snapshot["local"]["mods"],
                local_summary=snapshot["local"]["summary"],
                has_any_mods=self._has_any_mods(snapshot["local"]),
            )
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="installed_load_failed")

    def adopt_existing(self) -> dict[str, Any]:
        """把磁盘上已存在、且能对上 Registry 的模组与加载器登记进安装记录。

        模组会访问 GitHub Release 挑出该记的发布版本；磁盘上检测到、记录里没有的加载器只查索引
        自带的发布数据。登记后它们和普通安装的没有区别。
        `changed` 只表示这次有没有改动安装记录，供前端决定是否重画列表。
        """
        try:
            adopted = self._adopt_existing(self._current_service())
            return self._success(changed=bool(adopted))
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="adopt_existing_failed")

    def set_integrity_suppressed(self, path: str = "", suppressed: bool = True) -> dict[str, Any]:
        """把某个文件加入/移出「抑制损坏提示」名单（存**游戏目录**的 `SprocketModManager/suppression.json`）。

        抑制只影响**提示**：判定仍然是实时的，界面要把"被抑制"和"正常"区分开。
        键跟着**身份**走：文件有归属就用 `<package id>:<文件名>`，无归属才用规范相对路径 ——
        这样改名、在 `Mods/` 与 `Plugins/` 之间挪动、被重新认领都不会丢抑制。
        """
        try:
            relative = canonical_relative(str(path or "").strip())
            if not relative:
                raise ModToggleError("需要给出要抑制的文件路径")
            game_path = self._resolved_game_path()

            key = suppression_key_of(relative, [self._package_for_file(relative)])
            store = store_for(game_path)
            entries = store.load()
            # 同一个文件的键只有一种写法：删就删那一把键。
            kept = [item for item in entries if suppression_keys([item]) != suppression_keys([key])]
            if suppressed:
                kept.append(key)
            store.save(kept)
            saved = store.load()
            LOGGER.info("corruption suppression updated path=%s key=%s suppressed=%s", relative, key, suppressed)
            return self._success(
                suppressed=saved,
                installed=self._installed_data(),
            )
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="suppress_integrity_failed")

    def _package_for_file(self, relative: str) -> str:
        """这个受管文件属于哪个包（没有归属就返回空，抑制键退回路径形式）。"""
        try:
            for package_id, info in self._installed(self._current_service()).items():
                for item in info.get("files") or ():
                    if isinstance(item, str) and canonical_relative(item) == relative:
                        return str(package_id)
        except (ModManagerError, OSError, ValueError) as exc:
            LOGGER.warning("could not resolve the owner of %s: %s", relative, exc)
        return ""

    @staticmethod
    def _same_suppression(entry: str, key: str, relative: str) -> bool:
        """`entry` 与 `key` 是否是同一把键（同一个文件的抑制键只有一种写法）。"""
        return suppression_keys([str(entry).strip()]) == suppression_keys([key])

    def verify_installed(self) -> dict[str, Any]:
        """逐文件强制重算 SHA-256 并报告（不写状态文件；判断是实时的）。"""
        try:
            game_path = self._resolved_game_path()
            if not self._mutation_lock.acquire(blocking=False):
                raise ModToggleError("另一个模组操作正在进行，请稍后再试")
            try:
                result = self._current_service().verify_installed(
                    game_path,
                    suppressed=self._suppressed_paths(),
                )
            finally:
                self._mutation_lock.release()
            return self._success(
                corrupted=result["corrupted"],
                modified=result.get("modified", []),
                missing=result["missing"],
                checked=result["checked"],
                installed=self._installed_data(),
            )
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="verify_installed_failed")

    def get_local_mods(self, hashes: bool = False) -> dict[str, Any]:
        """本地 DLL 清单：静态元数据 + Registry 匹配 + 安装记录归属 + 禁用状态。

        默认不算 SHA-256（列表刷新要快）；需要摘要的调用方显式传 `hashes=true`。
        """
        try:
            service = self._current_service()
            return self._success(**self._local_mods_payload(service, hashes=bool(hashes)))
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="local_mods_failed")

    def toggle_mod(self, path: str, enabled: bool = True) -> dict[str, Any]:
        """启用/禁用模组（重命名，重启生效）。

        **按包生效**：如果这个文件属于某个已记录的包，禁用/启用会作用于**该包在 `Mods`/`Plugins` 下的
        全部可执行文件**，`UserLibs` 不动（那是被别人引用的库，改名会连累依赖者）。
        """
        try:
            game_path = self._resolved_game_path()
            service = self._current_service()
            roots = self._toggle_directories(service, game_path)
            target = resolve_mod_path(game_path, str(path), roots)
            targets = self._package_toggle_targets(service, game_path, target, roots)
            if not self._mutation_lock.acquire(blocking=False):
                raise ModToggleError("另一个模组操作正在进行，请稍后再试")
            toggled: list[str] = []
            try:
                for item in targets:
                    moved = apply_enabled(item, bool(enabled))
                    toggled.append(moved.relative_to(game_path).as_posix())
                    # 同步安装记录：状态记规范路径，改名只翻 disabled（`.dll`/`.dll.disable` 同一逻辑文件）。
                    try:
                        service.rename_managed_file(
                            game_path,
                            item.relative_to(game_path).as_posix(),
                            moved.relative_to(game_path).as_posix(),
                            disabled=not bool(enabled),
                        )
                    except (ModManagerError, OSError, ValueError) as exc:
                        LOGGER.warning("could not sync installed state after renaming %s: %s", item.name, exc)
            finally:
                self._mutation_lock.release()
            payload = self._local_mods_payload(service)
            return self._success(
                toggled=toggled[-1] if toggled else target.relative_to(game_path).as_posix(),
                toggled_paths=toggled,
                restart_required=True,
                **payload,
            )
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="toggle_mod_failed")

    def open_mod_location(self, path: str = "") -> dict[str, Any]:
        """在资源管理器里定位磁盘上的这个模组文件。

        只接受游戏目录内的相对路径（与安装记录同一套写法）：界面不该能点名打开任意位置。
        `UserLibs` / `AutoTranslator` 下的条目没有改名动作可做，但同样值得直接跳过去看。
        """
        try:
            game_path = self._resolved_game_path()
            relative = validate_relative_path(str(path or "").strip())
            reveal_in_file_manager(game_path / Path(*relative.parts))
            return self._success(path=relative.as_posix())
        except (ModManagerError, OSError, ValueError) as exc:
            return self._failure(exc, code="open_mod_location_failed")

    def _installed_ids(self, service: ModManagerService | None, game_path: Path) -> tuple[str, ...]:
        """安装记录里的包 id；读不出来就当作「什么都没装」，标识符退回自己的传统目录。"""
        if service is None:
            return ()
        try:
            return tuple(service.installed(game_path))
        except (ModManagerError, OSError, ValueError) as exc:
            LOGGER.warning("could not read install state for %s: %s", game_path, exc)
            return ()

    @staticmethod
    def _capabilities(service: ModManagerService | None) -> dict[str, object]:
        environment = getattr(service, "environment", None) if service is not None else None
        capabilities = getattr(environment, "capabilities", None)
        return dict(capabilities) if isinstance(capabilities, dict) else {}

    def _toggle_directories(self, service: ModManagerService | None, game_path: Path) -> tuple[str, ...]:
        """当前环境里可启用/禁用的目录：活跃标识符声明类型可切换的那些。"""
        registry = service.registry if service is not None else None
        packages = registry.packages if registry is not None else ()
        return toggle_directories(
            game_path,
            packages,
            self._installed_ids(service, game_path),
            self._capabilities(service),
        )

    def _package_toggle_targets(
        self,
        service: ModManagerService,
        game_path: Path,
        target: Path,
        roots: tuple[str, ...],
    ) -> list[Path]:
        """点一个文件要作用于哪些文件：属于某个包就作用到该包在受管目录下的全部可执行文件。

        找不到归属（仅本地模组）时只作用它自己。用户库一律跳过——那是被别的模组引用的库。
        """
        try:
            canonical = canonical_relative(target.relative_to(game_path).as_posix())
            packages = service.installed(game_path)
        except (ModManagerError, OSError, ValueError) as exc:
            LOGGER.warning("could not read install state while toggling %s: %s", target, exc)
            return [target]

        for package in packages.values():
            files = [item for item in package.get("files", ()) if isinstance(item, str)]
            if canonical.casefold() not in {item.casefold() for item in files}:
                continue
            resolved: list[Path] = []
            for relative in files:
                if not is_in_roots(str(relative), roots):
                    continue
                for candidate in (game_path / relative, game_path / (relative + ".disable")):
                    if candidate.is_file():
                        resolved.append(candidate)
                        break
            return resolved or [target]
        return [target]

    def _resolved_game_path(self) -> Path:
        value = effective_game_path(self.config)
        if not value:
            raise ModToggleError("尚未配置有效的 Sprocket 路径")
        game_path = Path(value).expanduser()
        if not (game_path / "Sprocket.exe").is_file():
            raise ModToggleError("Sprocket 路径无效")
        return game_path

    def _resolved_game_path_or_none(self) -> Path | None:
        """读抑制名单时用：路径还没配好就当作"没有名单"，不抛异常。"""
        try:
            return self._resolved_game_path()
        except ModToggleError:
            return None

    def _local_mods_payload(
            self,
            service: ModManagerService | None = None,
            *,
            hashes: bool = False,
            installed: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        try:
            game_path = self._resolved_game_path()
        except ModToggleError:
            empty: list[dict[str, Any]] = []
            return {"mods": empty, "summary": summarize([])}

        records = self._installed(service) if installed is None else installed
        managed = {
            relative.replace("\\", "/").casefold(): package_id
            for package_id, package in records.items()
            for relative in package.get("files", ())
            if isinstance(relative, str)
        }
        registry = service.registry if service is not None else None
        packages = registry.packages if registry is not None else ()
        local = scan_local_mods(
            game_path,
            managed,
            packages,
            installed=tuple(records),
            capabilities=self._capabilities(service),
            compute_hashes=hashes,
        )
        # 扫描自己已经落盘缓存（`scan_local_mods` 末尾 flush）；这里再兜一次，覆盖"同一请求里先认领、
        # 后扫描"的顺序问题——认领也会新增缓存条目。
        flush_metadata_cache()
        return {"mods": [mod.as_dict() for mod in local], "summary": summarize(local)}

    def _adopt_existing(self, service: ModManagerService) -> list[dict[str, Any]]:
        value = effective_game_path(self.config)
        if not value:
            return []
        game_path = Path(value).expanduser()
        if not (game_path / "Sprocket.exe").is_file():
            return []
        if not self._mutation_lock.acquire(blocking=False):
            return []
        try:
            adopted = [
                {
                    "id": record.package_id,
                    "name": record.name,
                    "version": record.version,
                    "files": list(record.files),
                }
                for record in service.adopt_existing(game_path)
            ]
            if adopted:
                # 认领会往记录里写加载器：加载器清单变了，环境读数必须重来一次。
                self._environment_monitor.invalidate()
            return adopted
        finally:
            self._mutation_lock.release()

    def _has_any_mods(self, local: dict[str, Any] | None = None) -> bool:
        """环境里是否存在**模组**：模组目录下至少有一个可加载（或被禁用）的 DLL。

        用户库与插件不算——「新安装推荐」的星标只在游戏还没有模组时出现。有扫描快照时直接用它
        （判据完全相同：同一套 `is_loadable_path`/`is_disabled_path`，类别取自所在目录），
        省掉一次目录遍历；没有快照（比如首次加载前的探测）才回退到走目录。
        """
        if local is not None:
            return any(
                str(mod.get("kind")) == MELON_KIND_MODS for mod in local.get("mods", ())
            )
        value = effective_game_path(self.config)
        if not value:
            return False
        game_path = Path(value).expanduser()
        if not (game_path / "Sprocket.exe").is_file():
            return False
        service = self.service
        registry = service.registry if service is not None else None
        packages = registry.packages if registry is not None else ()
        targets = scan_targets(
            game_path,
            packages,
            self._installed_ids(service, game_path),
            self._capabilities(service),
        )
        for _identifier, directory in targets:
            if directory.type.kind != MELON_KIND_MODS:
                continue
            if any(
                is_loadable_path(path) or is_disabled_path(path)
                for path in direct_files(game_path / directory.path)
            ):
                return True
        return False

    def _unrecognized_mods(
            self,
            service: ModManagerService,
            *,
            local: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """磁盘上存在、但没有安装记录归属的 DLL（含 Plugins、含被禁用的条目）。

        返回 ``{"name", "path"}``；完整身份信息见 ``local_mods``。
        """
        snapshot = local if local is not None else self._local_mods_payload(service)
        mods = snapshot["mods"]
        return [
            {"name": str(mod["name"]), "path": str(mod["path"])}
            for mod in mods
            if not mod["installed_package_id"]
        ]

    def get_package_readme(self, package_id: str, refresh: bool = False) -> dict[str, Any]:
        try:
            service = self._current_service()
            package = self._package(service, str(package_id))
            readme = service.github.repository_readme(
                package.repository,
                refresh=bool(refresh),
            )
            return self._success(
                package_id=package.id,
                html=readme.html,
                page_url=readme.page_url,
            )
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc, code="readme_load_failed")

    def open_readme_link(self, package_id: str, url: str) -> dict[str, Any]:
        try:
            self._package(self._current_service(), str(package_id))
            parsed = urlparse(str(url))
            if parsed.scheme != "https" or not parsed.hostname or len(str(url)) > 4096:
                raise ValueError("README links must use HTTPS")
            webbrowser.open(str(url))
            return self._success()
        except (ModManagerError, OSError, RuntimeError, ValueError) as exc:
            return self._failure(exc, code="open_url_failed")
