from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..domain.compatibility import INCOMPATIBLE, CapabilityEnvironment, release_verdict
from ..domain.errors import ResolutionError
from ..domain.models import RegistryPackage, ReleaseInfo, ResolvedPackage, ResolutionPlan
from ..domain.registry import Registry
from ..domain.semver import satisfies
from ..infrastructure.github import GitHubClient
from ..utilities.dependencies import dependencies_for_release


@dataclass(frozen=True)
class _DeadEnd:
    """搜索中某一步「某个包在当前约束下没有候选版本」。

    报错要靠它说清是哪条约束卡住的：最后一次这样的失败通常就是根因，
    而最初那层约束（只有根包 `*`）说明不了任何问题。
    """

    depth: int
    package_id: str
    constraints: tuple[str, ...]
    available: tuple[str, ...]
    constraint_only: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Releases:
    """一个包的全部可安装版本，以及其中只因环境被淘汰的那些。"""

    installable: tuple[ReleaseInfo, ...] = ()
    env_blocked: tuple[ReleaseInfo, ...] = ()


class DependencySolver:
    """模组之间的依赖求解；给了环境就顺手淘汰跑不了这个环境的版本。

    环境过滤覆盖**整条链**（根包与依赖都用同一套判定）：一个版本的环境声明只要有一条
    不通过就不会被选中，因此计划里的依赖也一定是能跑的。`pinned` 里的包例外 ——
    那是调用方（界面）明确点名的版本，选中它是用户的决定，不再用环境把它筛掉。
    """

    def __init__(
            self,
            registry: Registry,
            github: GitHubClient,
            environment: CapabilityEnvironment | None = None,
            pinned: frozenset[str] = frozenset(),
            installed: frozenset[str] = frozenset(),
    ):
        self.registry = registry
        self.github = github
        self.environment = environment
        self.pinned = pinned
        # 目标游戏目录里已经装上的包 id：一个类型有多个供给者时，只能由已安装的那个决定目录。
        self.installed = installed
        self._cache: dict[str, _Releases] = {}

    def _env_blocks(self, package: RegistryPackage, release: ReleaseInfo) -> bool:
        if self.environment is None or package.id in self.pinned:
            return False
        return (
            release_verdict(
                self.environment, category=package.category, dependencies=release.dependencies
            )
            == INCOMPATIBLE
        )

    def _releases(self, package_id: str) -> _Releases:
        """该包的可安装版本，另标出其中只因环境被淘汰的那些。"""
        cached = self._cache.get(package_id)
        if cached is not None:
            return cached
        package = self.registry.get(package_id)
        installable: list[ReleaseInfo] = []
        blocked: list[ReleaseInfo] = []
        for release in self.github.releases(package):
            if not self.github.install_assets(package, release):
                continue
            installable.append(release)
            if self._env_blocks(package, release):
                blocked.append(release)
        result = _Releases(tuple(installable), tuple(blocked))
        self._cache[package_id] = result
        return result

    def _candidates(
            self,
            package_id: str,
            constraints: list[str],
            *,
            respect_environment: bool = True,
    ) -> tuple[ReleaseInfo, ...]:
        releases = self._releases(package_id)
        blocked_ids = (
            {release.id for release in releases.env_blocked} if respect_environment else set()
        )
        return tuple(
            release
            for release in releases.installable
            if release.id not in blocked_ids
            and all(satisfies(release.version, constraint) for constraint in constraints)
        )

    def _loader_for_type(
            self,
            package: RegistryPackage,
            file_type: str,
            declared_ids: frozenset[str] = frozenset(),
    ) -> RegistryPackage | None:
        """这个类型该跟着哪个加载器装；由声明的依赖、供给者和已安装集合共同决定。

        只有唯一供给者时就是它。有多个供给者时，包自己声明的依赖点名了其中一个就用它：
        载荷是针对那份运行时构建的，声明的依赖比目标目录里装了什么更准确。没有这样的
        声明才看已安装集合：只有恰好一个已安装才有答案，否则报错让用户先去加载器页挑
        一个。计划里每个类型最多只出现一个供给者。
        """
        candidates = self.registry.providers_for_type(file_type)
        if not candidates:
            return None
        if package.id in {candidate.id for candidate in candidates}:
            # 包自己就供给这个类型（加载器自己的载荷），不需要把自己列成依赖。
            return None
        if len(candidates) == 1:
            return candidates[0]
        for candidate in candidates:
            if candidate.id in declared_ids:
                return candidate
        installed = [candidate for candidate in candidates if candidate.id in self.installed]
        if len(installed) == 1:
            return installed[0]
        ids = ", ".join(candidate.id for candidate in candidates)
        if not installed:
            raise ResolutionError(
                f"no modloader provider is installed for install type '{file_type}'; "
                f"install one of {ids} first"
            )
        raise ResolutionError(
            f"several modloader providers are installed for install type '{file_type}'; "
            f"keep only one of {ids}"
        )

    def _dependencies_for(self, package: RegistryPackage, release: ReleaseInfo) -> tuple[dict[str, str], ...]:
        """声明的依赖 + 从静态安装类型推导出的加载器依赖。

        文件写着 `melonloader:mod` 就必须有供给这个类型的加载器在场上，所以它是一条真依赖：
        求解时一并装上，卸载时也按同一张依赖图判定能不能删。加载器自己供给的类型不算依赖。
        包声明的依赖已经点名了某个供给者时，那一条就是这条依赖，不再另加隐式依赖。

        只跳过本机能力：对本机能力的依赖由能力判定负责，装不了一个能力（例如游戏那根轴），
        也不该进依赖图。既不是包、也不是能力的 id 不是跳过，而是照旧报成解析不了的依赖。
        """
        declared = tuple(
            dependency
            for dependency in dependencies_for_release(package, release)
            if self.registry.has_package(str(dependency.get("id", "")))
            or not self.registry.knows_capability(str(dependency.get("id", "")))
        )
        resolved_ids = {str(item.get("id")) for item in declared}
        implicit: list[dict[str, str]] = []
        for file_type in package.declared_types():
            supplier = self._loader_for_type(package, file_type, frozenset(resolved_ids))
            if supplier is None or supplier.id in resolved_ids:
                continue
            resolved_ids.add(supplier.id)
            implicit.append({"id": supplier.id, "version": "*", "when": "*"})
        return declared + tuple(implicit)

    @staticmethod
    def _explain(
            root: RegistryPackage,
            root_range: str,
            dead_ends: list[_DeadEnd],
            constraints: dict[str, list[str]],
            environment: CapabilityEnvironment | None,
    ) -> str:
        """报出真正卡住的那条约束：最靠后的那次「无候选」+ 该包实际有哪些版本。

        整包都没有候选、且原因是环境（而不是版本约束）时说清这一点：用户得知道
        「这些版本都要求另一个 Sprocket / MelonLoader」，否则只会以为是依赖写错了。

        没有死路可报（理论上不该发生）时退回最初的约束表，至少给出请求的包。
        """
        if not dead_ends:
            return ", ".join(
                f"{package_id} {' & '.join(ranges)}" for package_id, ranges in constraints.items()
            )
        blocked = max(dead_ends, key=lambda item: (item.depth, len(item.constraints)))
        available = ", ".join(str(version) for version in blocked.available) or "none"
        if blocked.package_id == root.id:
            summary = f"{root.id} {root_range}; available releases: {available}"
        else:
            summary = (
                f"{root.id} {root_range} -> {blocked.package_id} {' & '.join(blocked.constraints)}"
                f"; available releases: {available}"
            )
        if blocked.constraint_only and not blocked.available and environment is not None:
            # 约束本身是能满足的，是环境把这些版本全筛掉了 —— 这才是要报的原因。
            versions = ", ".join(str(version) for version in blocked.constraint_only)
            summary += (
                f"; {versions} requires another environment"
                f" (this machine: {environment.label()})"
            )
        return summary

    def resolve(self, root_id: str, root_range: str = "*") -> ResolutionPlan:
        root = self.registry.resolve_identifier(root_id)
        constraints: dict[str, list[str]] = defaultdict(list)
        constraints[root.id].append(root_range)
        assignments: dict[str, ReleaseInfo] = {}
        dependencies: dict[str, tuple[str, ...]] = {}
        dead_ends: list[_DeadEnd] = []

        def search(
                current_constraints: dict[str, list[str]],
                current_assignments: dict[str, ReleaseInfo],
                current_dependencies: dict[str, tuple[str, ...]],
        ) -> tuple[dict[str, ReleaseInfo], dict[str, tuple[str, ...]]] | None:
            unresolved = [package_id for package_id in current_constraints if package_id not in current_assignments]
            if not unresolved:
                return current_assignments, current_dependencies

            candidate_sets = {
                package_id: self._candidates(package_id, current_constraints[package_id])
                for package_id in unresolved
            }
            blocked = False
            for package_id, candidates in candidate_sets.items():
                if candidates:
                    continue
                blocked = True
                unconstrained = self._candidates(package_id, ())
                dead_ends.append(
                    _DeadEnd(
                        depth=len(current_assignments),
                        package_id=package_id,
                        constraints=tuple(current_constraints[package_id]),
                        available=tuple(item.version for item in unconstrained),
                        constraint_only=tuple(
                            item.version
                            for item in self._candidates(
                                package_id,
                                current_constraints[package_id],
                                respect_environment=False,
                            )
                        ),
                    )
                )
            if blocked:
                return None

            package_id = min(unresolved, key=lambda item: len(candidate_sets[item]))
            for release in candidate_sets[package_id]:
                package = self.registry.get(package_id)
                next_assignments = dict(current_assignments)
                next_assignments[package_id] = release
                next_constraints = {key: list(value) for key, value in current_constraints.items()}
                active_dependencies = self._dependencies_for(package, release)
                next_dependencies = dict(current_dependencies)
                next_dependencies[package_id] = tuple(item["id"] for item in active_dependencies)
                valid = True
                for dependency in active_dependencies:
                    dependency_id = dependency["id"]
                    next_constraints.setdefault(dependency_id, []).append(dependency["version"])
                    assigned = next_assignments.get(dependency_id)
                    if assigned and not satisfies(assigned.version, dependency["version"]):
                        valid = False
                        break
                if not valid:
                    continue
                result = search(next_constraints, next_assignments, next_dependencies)
                if result:
                    return result
            return None

        result = search(dict(constraints), assignments, dependencies)
        if not result:
            details = self._explain(root, root_range, dead_ends, constraints, self.environment)
            raise ResolutionError(f"no compatible release set found ({details})")
        assignments, dependencies = result

        ordered_ids: list[str] = []
        visiting: set[str] = set()

        def visit(package_id: str) -> None:
            if package_id in ordered_ids:
                return
            if package_id in visiting:
                raise ResolutionError(f"dependency cycle reached while resolving {package_id}")
            visiting.add(package_id)
            for dependency_id in dependencies.get(package_id, ()):
                visit(dependency_id)
            visiting.remove(package_id)
            ordered_ids.append(package_id)

        visit(root.id)
        packages = tuple(
            ResolvedPackage(
                package=self.registry.get(package_id),
                release=assignments[package_id],
                dependency_ids=dependencies.get(package_id, ()),
            )
            for package_id in ordered_ids
        )
        return ResolutionPlan(root.id, packages)
