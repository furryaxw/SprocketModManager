"""安装计划：模组操作的唯一出口。

每行一个包，动作是「装某版 / 不安装 / 卸载」。永不拒绝：冲突的包标红出局，结果可以是空安装
列表；只有「用户点名的那一版无法与别的点名项共存」才算没有有效解，那时调用方保留上一个有效解。

版本与兼容性的求解只有一处（`solver`），判定只有一处（`releases`）—— 这里只负责把它们摆成
计划：每行的动作、版本、红行原因、连带卸载、以及下拉要用的兼容性阵列。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..domain.compatibility import CapabilityEnvironment, INCOMPATIBLE
from ..domain.errors import ModManagerError
from ..domain.models import ReleaseInfo, RegistryPackage, ResolvedPackage
from ..domain.registry import Registry
from ..infrastructure.github import GitHubClient
from ..infrastructure.installer import Installer
from ..utilities.dependencies import dependencies_for_release
from .releases import release_verdicts
from .solver import DependencySolver, Dropped

# 版本下拉里的第三项：未装显示「不安装」、已装显示「卸载」，后端只认这两个字面量。
CHOICE_SKIP = "skip"
CHOICE_REMOVE = "remove"

ACTION_INSTALL = "install"
ACTION_SKIP = "skip"
ACTION_REMOVE = "remove"


@dataclass(frozen=True)
class ChangeRequest:
    """用户这一批操作：选中的包、改过的选择，以及重装/强制这两个标记。"""

    package_ids: tuple[str, ...]
    choices: Mapping[str, str] = field(default_factory=dict)
    reinstall: bool = False
    force: bool = False


class ChangePlanner:
    def __init__(
            self,
            *,
            registry: Registry,
            github: GitHubClient,
            environment: CapabilityEnvironment,
            state: Mapping[str, Mapping[str, Any]],
            modloaders: Sequence[str] = (),
    ):
        self.registry = registry
        self.github = github
        self.environment = environment
        # 安装记录（包 id -> 记录）：世界就是它，卸载闭包也从它算。
        self.state = dict(state)
        self.modloaders = frozenset(str(item) for item in modloaders)
        self._versions: dict[str, list[dict[str, Any]]] = {}

    def plan(self, request: ChangeRequest) -> dict[str, Any]:
        ids = [self._canonical(item) for item in request.package_ids]
        ids = list(dict.fromkeys(item for item in ids if item))
        removed = {item for item in ids if request.choices.get(item) == CHOICE_REMOVE}
        skipped = {item for item in ids if request.choices.get(item) == CHOICE_SKIP}
        roots: dict[str, str] = {}
        for item in ids:
            if item in removed or item in skipped or not self.registry.has_package(item):
                continue
            choice = str(request.choices.get(item) or "")
            roots[item] = f"={choice}" if choice else "*"
        installed = {
            item: str(info.get("version") or "") for item, info in self.state.items()
        }
        resolver = DependencySolver(
            self.registry,
            self.github,
            environment=self.environment,
            pinned=frozenset(item for item, value in roots.items() if value.startswith("=")),
            installed=installed,
        )
        tolerant = resolver.resolve_tolerant(roots, excluded=skipped)
        if tolerant.plan is None:
            # 用户点名的那一版装不成：不产生新计划，由调用方保留上一个有效解。
            return {
                "no_solution": True,
                "message": self._no_solution_message(roots),
                # 点名的包与各自要的区间：界面照它按当前语言写那句「凑不出有效解」。
                "roots": [
                    {"id": item, "range": value} for item, value in sorted(roots.items())
                ],
                "lines": [],
                "install": {},
                "remove": [],
            }
        resolved = tolerant.plan.by_id()
        dropped = {item.package_id: item for item in tolerant.dropped}
        remove_ids, replacements, remove_with = self._remove_plan(removed)
        # 这一份计划里的依赖关系：一行自己依赖谁，以及谁依赖这一行 —— 只在计划内部的边上算。
        depends_on = {
            item.package.id: tuple(str(dependency) for dependency in item.dependency_ids)
            for item in tolerant.plan.packages
        }
        required_by: dict[str, list[str]] = {}
        for package_id, dependency_ids in depends_on.items():
            for dependency_id in dependency_ids:
                required_by.setdefault(dependency_id, []).append(package_id)
        # 已经在盘上、这一批又不动它的包不进页面：它没有要落的盘，只在依赖关系里被点名。
        # 点名的那些照旧出框（用户要的就是「装这一版」这件事本身）。
        named_ids = set(ids)
        settled = {
            item.package.id for item in tolerant.plan.packages
            if item.package.id not in named_ids
            and installed.get(item.package.id) == str(item.release.version)
        }
        # 一个包一张卡片：用户点名的按他给的顺序排在前面，这一批会装进来的依赖跟在后面。
        # `named` 只服务执行那一侧 —— 入队的是点名的那些行，依赖各自跟着自己的依赖者进来。
        row_ids = list(ids)
        row_ids.extend(
            item.package.id for item in tolerant.plan.packages
            if item.package.id not in named_ids and item.package.id not in settled
        )
        lines: list[dict[str, Any]] = []
        for item in row_ids:
            lines.append(
                self._line(
                    item,
                    request=request,
                    named=item in named_ids,
                    removed=item in remove_ids,
                    skipped=item in skipped,
                    chosen=resolved.get(item),
                    dropped=dropped.get(item),
                    skipped_ids=skipped,
                    dropped_ids=set(dropped),
                    planned=set(resolved),
                    remove_with=remove_with.get(item, ()),
                    replacements=replacements,
                    depends_on=depends_on.get(item, ()),
                    required_by=tuple(sorted(required_by.get(item, ()))),
                )
            )
        # `install` 是这一批会落到磁盘上的全部包（点名的那些，加上它们带进来的依赖）；
        # 行只有用户点的那几行，执行时按行钉版本、依赖各自跟着带进来。
        install = {
            item.package.id: str(item.release.version)
            for item in tolerant.plan.packages
            if item.package.id not in settled
        }
        install.update(replacements)
        return {
            "no_solution": False,
            "lines": lines,
            "install": {item: install[item] for item in sorted(install)},
            "remove": sorted(remove_ids),
            # 用户标成「不安装」的那些：执行时它们连当依赖都不算。
            "excluded": sorted(skipped),
            # 这一批里的包各自推荐的、现在又装得上的那些：页面列出来让用户勾，勾中的跟着一起装。
            "recommendations": self._recommendations(
                tolerant.plan.packages, covered=set(resolved), installed=installed
            ),
            # 为了让卸载目标走得掉而换版本的依赖者：页面把它作为一条警告摆出来。
            "replaced_by": [
                {"id": item, "version": version} for item, version in sorted(replacements.items())
            ],
        }

    def _recommendations(
            self,
            packages: Sequence[ResolvedPackage],
            *,
            covered: set[str],
            installed: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        """这一批里每个包声明的推荐：没被这一批覆盖、注册表认得、而且现在真装得上的那些。

        推荐来自计划里的每个包（依赖带进来的也算，它跟用户点的那个包一起装）。装哪一版由这里的
        一次求解给出 —— 跟计划本身同一套口径。要不要一起装由用户在计划页勾。
        """
        found: dict[str, dict[str, Any]] = {}
        for item in packages:
            for raw_id in item.package.recommendations:
                recommendation_id = str(raw_id)
                if recommendation_id in covered:
                    continue
                if not self.registry.has_package(recommendation_id):
                    continue
                entry = found.get(recommendation_id)
                if entry is None:
                    entry = self._recommendation(recommendation_id, installed)
                    if entry is None:
                        continue
                    found[recommendation_id] = entry
                if item.package.id not in entry["recommended_by"]:
                    entry["recommended_by"].append(item.package.id)
        return list(found.values())

    def _recommendation(
            self,
            package_id: str,
            installed: Mapping[str, str],
    ) -> dict[str, Any] | None:
        """这个推荐现在会装成哪一版；装不成、或者装着的就是那一版，就没有可推荐的。"""
        solver = DependencySolver(
            self.registry,
            self.github,
            environment=self.environment,
            pinned=frozenset({package_id}),
            installed=installed,
        )
        try:
            plan = solver.resolve(package_id)
        except (ModManagerError, OSError, ValueError):
            return None
        resolved = plan.by_id().get(package_id)
        if resolved is None:
            return None
        version = str(resolved.release.version)
        if installed.get(package_id) == version:
            return None
        package = self.registry.get(package_id)
        return {
            "id": package_id,
            "name": package.name,
            "display_name": dict(package.display_name),
            "version": version,
            "recommended_by": [],
        }

    def _line(
            self,
            package_id: str,
            *,
            request: ChangeRequest,
            named: bool,
            removed: bool,
            skipped: bool,
            chosen: ResolvedPackage | None,
            dropped: Dropped | None,
            skipped_ids: set[str],
            dropped_ids: set[str],
            planned: set[str],
            remove_with: Sequence[str],
            replacements: Mapping[str, str],
            depends_on: Sequence[str],
            required_by: Sequence[str],
    ) -> dict[str, Any]:
        info = self.state.get(package_id) or {}
        installed_version = str(info.get("version") or "")
        package = (
            self.registry.get(package_id) if self.registry.has_package(package_id) else None
        )
        versions = self._version_list(package) if package is not None else []
        line: dict[str, Any] = {
            "id": package_id,
            "name": package.name if package is not None else package_id,
            "display_name": dict(package.display_name) if package is not None else {},
            "installed_version": installed_version,
            "named": named,
            "action": ACTION_SKIP,
            "version": None,
            "reinstall": False,
            "verdict": "",
            "problems": [],
            # 依赖与被依赖都只在计划内部的边上算：界面照它写「依赖 xxx / 被 xxx 依赖」。
            "depends_on": [str(item) for item in depends_on],
            "required_by": [str(item) for item in required_by],
            "remove_with": [],
            "versions": versions,
        }
        if package is None:
            line["problems"].append({
                "kind": "no-candidate",
                "text": f"{package_id} is not in the registry",
                "others": [],
            })
            return line
        if removed:
            line["action"] = ACTION_REMOVE
            line["version"] = installed_version
            line["remove_with"] = [item for item in remove_with if item != package_id]
            return line
        if skipped:
            return line
        if chosen is None:
            line["problems"], line["ignored"] = self._explain_gap(
                package,
                skipped_ids=skipped_ids,
                dropped_ids=dropped_ids,
                planned=planned,
                dropped=dropped,
            )
            return line
        version = str(chosen.release.version)
        line["action"] = ACTION_INSTALL
        line["version"] = version
        line["reinstall"] = bool(request.reinstall) or version == installed_version
        line["verdict"] = next(
            (item["verdict"] for item in versions if item["version"] == version), ""
        )
        gaps = self._dependency_gaps(
            package,
            version,
            skipped_ids=skipped_ids,
            dropped_ids=dropped_ids,
            planned=planned,
        )
        # 落盘时要照这一行执行：这些依赖是计划里明确留着的缺口，不许在执行时再被拉回来。
        line["ignored"] = [item["id"] for item in gaps]
        # 缺口只写这一处：`problems` 既是红行的原因，也是页面上那行说明的唯一来源。
        line["problems"].extend({
            "kind": item["kind"],
            "text": f"{item['id']} {item['version']}".strip(),
            "others": [item["id"]],
        } for item in gaps)
        return line

    def _explain_gap(
            self,
            package: RegistryPackage,
            *,
            skipped_ids: set[str],
            dropped_ids: set[str],
            planned: set[str],
            dropped: Dropped | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """这一行为什么是红的。

        它自己声明的依赖里有缺口（注册表没有、或这次没被解进来）就报那条依赖 —— 「装的这一版要
        的东西不在」比「和某某冲突」更贴近用户看到的东西。没有缺口才落到求解器的冲突上。
        """
        gaps: list[dict[str, str]] = []
        for dependency in package.dependencies:
            dependency_id = str(dependency.get("id", ""))
            if not self.registry.has_package(dependency_id):
                kind = "missing-dependency"
            elif dependency_id in planned or dependency_id in self.state:
                continue
            elif dependency_id in skipped_ids:
                kind = "blocked-by-skip"
            elif dependency_id in dropped_ids:
                kind = "conflict"
            else:
                # 声明的依赖这次没被解进来：是「两边争同一个版本」还是「这条依赖根本没得装」，
                # 只有求解器知道，照它给的缘故写。
                kind = dropped.reason if dropped is not None else "missing-dependency"
            gaps.append({
                "id": dependency_id,
                "version": str(dependency.get("version", "")),
                "kind": kind,
            })
        if not gaps:
            return (
                [{
                    "kind": dropped.reason if dropped is not None else "conflict",
                    "text": self._conflict_text(dropped),
                    "others": list(dropped.others) if dropped is not None else [],
                }],
                [],
            )
        return (
            [
                {
                    "kind": item["kind"],
                    "text": f"{item['id']} {item['version']}".strip(),
                    "others": [item["id"]],
                }
                for item in gaps
            ],
            [item["id"] for item in gaps],
        )

    def _dependency_gaps(
            self,
            package: RegistryPackage,
            version: str,
            *,
            skipped_ids: set[str],
            dropped_ids: set[str],
            planned: set[str],
    ) -> list[dict[str, str]]:
        """这一版要的依赖里，既不在这份计划里、也没装在磁盘上的那些。

        用户把它标成「不安装」、或被冲突带出局的，都算缺口 —— 这一版照装，缺口照实标红。
        """
        release = self._release(package, version)
        if release is None:
            return []
        gaps: list[dict[str, str]] = []
        for dependency in dependencies_for_release(package, release):
            dependency_id = str(dependency.get("id", ""))
            if not self.registry.has_package(dependency_id):
                continue
            if dependency_id in planned or dependency_id in self.state:
                continue
            if dependency_id in skipped_ids:
                kind = "blocked-by-skip"
            elif dependency_id in dropped_ids:
                kind = "conflict"
            else:
                kind = "missing-dependency"
            gaps.append({"id": dependency_id, "version": str(dependency.get("version", "")), "kind": kind})
        return gaps

    def _remove_plan(
            self,
            targets: set[str],
    ) -> tuple[set[str], dict[str, str], dict[str, list[str]]]:
        """卸载目标 -> (要卸的包, 给依赖者换上的版本, 每个目标的连带卸载)。

        被挡住时先给依赖者找一轮「不依赖卸载目标」的版本装上；找不到就连它一起卸。
        """
        if not targets:
            return set(), {}, {}
        removing = set(targets)
        while True:
            closure, blocked = Installer.removal_closure(
                {"packages": dict(self.state)}, removing, modloaders=self.modloaders
            )
            replacements: dict[str, str] = {}
            cascaded: set[str] = set()
            for required_by in blocked.values():
                for dependent in required_by:
                    if dependent in removing:
                        continue
                    replacement = self._replacement_version(dependent, closure)
                    if replacement is None:
                        cascaded.add(dependent)
                    else:
                        replacements[dependent] = replacement
            if not cascaded:
                remove_with = {
                    item: sorted(closure - {item}) for item in sorted(targets)
                }
                return closure, replacements, remove_with
            removing |= cascaded

    def _replacement_version(self, package_id: str, removing: set[str]) -> str | None:
        """这个包能不能换到一个不依赖被卸那些的版本：环境跑得起来的优先。"""
        if not self.registry.has_package(package_id):
            return None
        package = self.registry.get(package_id)
        entries = self._version_list(package)
        ordered = [item for item in entries if item["verdict"] != INCOMPATIBLE]
        ordered += [item for item in entries if item["verdict"] == INCOMPATIBLE]
        for entry in ordered:
            release = self._release(package, entry["version"])
            if release is None:
                continue
            dependencies = dependencies_for_release(package, release)
            if any(str(item.get("id", "")) in removing for item in dependencies):
                continue
            return entry["version"]
        return None

    def _version_list(self, package: RegistryPackage) -> list[dict[str, Any]]:
        cached = self._versions.get(package.id)
        if cached is None:
            cached = release_verdicts(
                package,
                registry=self.registry,
                github=self.github,
                environment=self.environment,
            )
            self._versions[package.id] = cached
        return cached

    def _release(self, package: RegistryPackage, version: str) -> ReleaseInfo | None:
        releases = (
            package.releases
            if package.releases is not None
            else self.github.releases(package)
        )
        return next((item for item in releases if str(item.version) == version), None)

    def _canonical(self, package_id: str) -> str:
        try:
            return self.registry.resolve_identifier(package_id).id
        except ModManagerError:
            return str(package_id)

    @staticmethod
    def _no_solution_message(roots: Mapping[str, str]) -> str:
        named = ", ".join(f"{item} {value}" for item, value in sorted(roots.items()))
        return f"{named}: no version set satisfies every named version"

    @staticmethod
    def _conflict_text(dropped: Dropped | None) -> str:
        others = ", ".join(dropped.others) if dropped is not None else ""
        return f"conflicts with {others}" if others else "conflicts with another selection"
