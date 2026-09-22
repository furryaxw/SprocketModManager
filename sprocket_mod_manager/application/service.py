from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Mapping

from .adoption import AdoptionRecord, ExistingModsAdopter
from .integrity import annotate, published_hashes
from .integrity import BROKEN_STATUSES
from .preparer import PlanPreparer
from .solver import DependencySolver
from ..domain.errors import ModManagerError, RegistryError
from ..infrastructure.mod_toggle import canonical_relative
from ..domain.models import PreparedPlan, ProgressCallback, ResolutionPlan
from ..domain.registry import Registry
from ..infrastructure.defaults import default_app_dir
from ..infrastructure.dll_metadata import cached_sha256, configure_metadata_backend
from ..infrastructure.github import GitHubClient
from ..infrastructure.http_client import HttpClient
from ..infrastructure.installer import Installer
from ..infrastructure.profiles import InstallerProfiles
from ..infrastructure.file_metadata import FileMetadataStore
from ..infrastructure.manager_paths import STATE_FILE_NAME, file_metadata_path, manager_state_dir
from ..infrastructure.state import StateStore
from ..infrastructure.registry_source import RegistrySourceLoader

LOGGER = logging.getLogger(__name__)


class ModManagerService:
    def __init__(self, app_dir: Path | None = None, token: str | None = None):
        self.app_dir = app_dir or default_app_dir()
        LOGGER.debug("initializing service app_dir=%s token_configured=%s", self.app_dir, bool(token))
        self.http = HttpClient(self.app_dir / "cache", token=token)
        self.github = GitHubClient(self.http)
        self._registry_loader = RegistrySourceLoader(self.http)
        self._profiles = InstallerProfiles(self.app_dir)
        # DLL 元数据缓存是**独立文件**（`<game>/SprocketModManager/file-metadata.json`）：
        # 解析很贵（冷启动约 2 秒），但缓存与安装记录都属于"这个游戏目录的状态"，
        # 一起放在游戏目录里，AppData 里不留任何游戏相关的东西。
        self._metadata_game_dir: Path | None = None
        self.registry: Registry | None = None
        # 上一次标注里失效的抑制条目（由调用方写回游戏目录的 suppression.json）。
        self._stale_suppressions: list[str] = []

    def _configure_cache_for(self, game_dir: Path) -> None:
        """把元数据缓存挂到该游戏的独立文件；游戏没变就不重挂（重挂会丢掉内存缓存）。"""
        resolved = game_dir.expanduser().resolve()
        if resolved == self._metadata_game_dir:
            return
        # 缓存放在 `<game>/SprocketModManager/file-metadata.json`（与安装记录同目录、不同文件），
        # 既满足"meta 放到另一个文件"，也不在 AppData 里留任何游戏相关的东西。
        configure_metadata_backend(FileMetadataStore(file_metadata_path(resolved)))
        self._metadata_game_dir = resolved

    def _installer_for(self, game_dir: Path) -> Installer:
        self._configure_cache_for(game_dir)
        return self._profiles.installer_for(game_dir)

    def load_registry(self, source: str | Path, *, refresh: bool = False) -> Registry:
        LOGGER.info("loading registry source=%s refresh=%s", source, refresh)
        registry = self._registry_loader.load(source, refresh=refresh)
        self.registry = registry
        LOGGER.info("registry loaded packages=%d", len(registry.packages))
        return registry

    def _require_registry(self) -> Registry:
        if not self.registry:
            raise RegistryError("registry is not loaded")
        return self.registry

    def resolve(self, identifier: str, version_range: str = "*") -> ResolutionPlan:
        LOGGER.debug("resolving package identifier=%s range=%s", identifier, version_range)
        registry = self._require_registry()
        package = registry.resolve_identifier(identifier)
        plan = DependencySolver(registry, self.github).resolve(package.id, version_range)
        LOGGER.info("resolved package=%s packages=%d", package.id, len(plan.packages))
        return plan

    def prepare(
            self,
            plan: ResolutionPlan,
            progress: ProgressCallback | None = None,
    ) -> PreparedPlan:
        return PlanPreparer(self.app_dir, self.http, self.github).prepare(plan, progress)

    def install(
            self,
            identifier: str,
            game_dir: Path,
            *,
            version_range: str = "*",
            progress: ProgressCallback | None = None,
            force_conflicts: bool = False,
    ) -> tuple[ResolutionPlan, list[str]]:
        LOGGER.info("install requested identifier=%s game_dir=%s", identifier, game_dir)
        plan = self.resolve(identifier, version_range)
        prepared = self.prepare(plan, progress)
        try:
            warnings = self._installer_for(game_dir).apply(
                prepared,
                game_dir,
                progress=progress,
                force_conflicts=force_conflicts,
            )
            LOGGER.info("install completed identifier=%s warnings=%d", identifier, len(warnings))
            return plan, warnings
        finally:
            PlanPreparer.discard(prepared)

    def remove(self, identifier: str, game_dir: Path) -> tuple[list[str], list[str]]:
        LOGGER.info("remove requested identifier=%s game_dir=%s", identifier, game_dir)
        registry = self._require_registry()
        package = registry.resolve_identifier(identifier)
        result = self._installer_for(game_dir).remove(package.id, game_dir)
        LOGGER.info("remove completed package=%s removed=%d warnings=%d", package.id, len(result[0]), len(result[1]))
        return result

    def installed(self, game_dir: Path, *, suppressed: Iterable[str] = ()) -> dict[str, dict[str, Any]]:
        """已安装包（磁盘优先），带**实时**的完整性判定。

        读取前先与磁盘对账：手工删掉/改名的 DLL 对应的记录会被清掉。这样列表永远来自
        "磁盘扫描 + DLL 元数据"，不会出现 `installed.json` 里残留的幽灵条目。

        `corrupted` 每次刷新现场算出来：磁盘 hash（(size, mtime) 快路径，
        没改过的文件只做一次 stat）与 Registry 里的**全部发布版本**资产 hash 现场比对。
        """
        installer = self._installer_for(game_dir)
        try:
            installer.reconcile(game_dir)
        except (ModManagerError, OSError, ValueError) as exc:
            LOGGER.warning("could not reconcile install state with %s: %s", game_dir, exc)
        state = installer.state_store.load()
        self._annotate_integrity(state, game_dir, suppressed=suppressed)
        return state["packages"]

    def stale_suppressions(self) -> list[str]:
        """上一次 `installed()` / `verify_installed()` 里**已失效**的抑制条目。

        抑制名单存在**游戏目录**的 `SprocketModManager/suppression.json`（见 `suppression_store`），
        这一层不写它；调用方（GUI/CLI）拿到这份清单后自己把失效条目从名单里删掉。
        失效 = 包记录没了、路径也没了。
        """
        return list(getattr(self, "_stale_suppressions", ()) or ())

    def verify_installed(self, game_dir: Path, *, suppressed: Iterable[str] = ()) -> dict[str, Any]:
        """强制重算每个已安装文件的 SHA-256 并报告（**不写状态文件**）。

        `corrupted` = 有发布数据、但磁盘内容不匹配任何发布版本；`modified` = 与安装记录不一致；
        `missing` = 文件不在。三者是不同的事实，界面上应该分开表达。
        """
        installer = self._installer_for(game_dir)
        installer.reconcile(game_dir)
        report = installer.verify(game_dir)
        state = installer.state_store.load()
        self._annotate_integrity(state, game_dir, suppressed=suppressed, hashes=report["hashes"])
        corrupted = sorted(
            relative
            for relative, entry in state["files"].items()
            if str(entry.get("integrity") or "") in BROKEN_STATUSES
        )
        LOGGER.info("verify completed checked=%d corrupted=%d modified=%d missing=%d",
                    report["checked"], len(corrupted), len(report["changed"]), len(report["missing"]))
        return {
            "checked": report["checked"],
            "corrupted": corrupted,
            "modified": report["changed"],
            "missing": report["missing"],
        }

    def _annotate_integrity(
            self,
            state: dict[str, Any],
            game_dir: Path,
            *,
            suppressed: Iterable[str] = (),
            hashes: Mapping[str, str] | None = None,
    ) -> None:
        annotate(
            state,
            game_dir=game_dir,
            published_by_package=published_hashes(self.registry.packages if self.registry else None),
            suppressed=suppressed,
            hash_provider=cached_sha256,
            hashes=hashes,
        )
        report = state.get("suppression")
        self._stale_suppressions = list(report.get("stale", ())) if isinstance(report, dict) else []

    def adopt_existing(self, game_dir: Path) -> tuple[AdoptionRecord, ...]:
        registry = self._require_registry()
        installer = self._installer_for(game_dir)
        return ExistingModsAdopter(self.github, installer).adopt(registry, game_dir)

    def rename_managed_file(
            self,
            game_dir: Path,
            old_relative: str,
            new_relative: str,
            *,
            disabled: bool,
    ) -> bool:
        """启用/禁用改名后同步安装记录，让管理器自己的缓存与磁盘保持一致。

        文件被改名时 `installed.json` 里仍记着旧路径：不修的话"已安装"与扫描结果会互相打架
        （包显示已安装但文件不存在、禁用后的条目丢掉归属）。这里把记录里的键搬到新路径，
        并标记 `disabled`，包级的 `files` 列表同步更新。
        """
        store = self._installer_for(game_dir).state_store
        state = store.load()
        # `.dll` 与 `.dll.disable` 是同一个逻辑文件：不搬键，只翻禁用标志
        # （`_to_storage` 落盘时也会把键归一成规范路径）。
        entry = state["files"].get(canonical_relative(old_relative))
        if entry is None:
            return False

        entry["disabled"] = bool(disabled)
        store.save(state)
        LOGGER.info("managed file toggled path=%s disabled=%s", old_relative, disabled)
        return True
