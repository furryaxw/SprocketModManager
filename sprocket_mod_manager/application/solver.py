from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.compatibility import (
    COMPATIBLE,
    INCOMPATIBLE,
    UNKNOWN,
    CapabilityEnvironment,
    range_allows,
    release_verdict,
)
from ..domain.errors import ResolutionError
from ..domain.models import RegistryPackage, ReleaseInfo, ResolvedPackage, ResolutionPlan
from ..domain.registry import Registry
from ..domain.semver import satisfies
from ..infrastructure.github import GitHubClient
from ..utilities.dependencies import dependencies_for_release


@dataclass(frozen=True)
class _Constraint:
    """一条版本约束，以及提这条约束的包。

    报错要指名冲突的双方，所以来源得跟着约束一起走 —— 只看区间字符串说不出是「谁要的」。
    """

    range: str
    source: str


@dataclass(frozen=True)
class _DeadEnd:
    """搜索中某一步「某个包在当前约束下没有候选版本」。

    报错要靠它说清是哪条约束卡住的：最后一次这样的失败通常就是根因，
    而最初那层约束（只有根包 `*`）说明不了任何问题。
    """

    depth: int
    package_id: str
    constraints: tuple[_Constraint, ...]
    available: tuple[str, ...]
    constraint_only: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dropped:
    """计划里出局的包：它与其他提要求的包互相不能共存，两边都不选边。"""

    package_id: str
    reason: str
    others: tuple[str, ...]


@dataclass(frozen=True)
class TolerantResolution:
    """宽容求解的结果。

    `plan` 为空表示连一个自洽的分配都算不出（用户点名的那一版被卡住了）；`plan.packages`
    为空表示算出来的解就是「什么都不装」。`unmet` 是让位掉的依赖需求：(提出要求的包, 被让位的包)。
    """

    plan: ResolutionPlan | None
    dropped: tuple[Dropped, ...] = ()
    unmet: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _Releases:
    """一个包的全部可安装版本，以及其中只因环境被淘汰的、与明确判为兼容的那些。"""

    installable: tuple[ReleaseInfo, ...] = ()
    env_blocked: tuple[ReleaseInfo, ...] = ()
    compatible: tuple[ReleaseInfo, ...] = ()


class DependencySolver:
    """模组之间的依赖求解；给了环境就顺手淘汰跑不了这个环境的版本。

    环境过滤覆盖**整条链**（根包与依赖都用同一套判定）：一个版本的环境声明只要有一条
    不通过就不会被选中，因此计划里的依赖也一定是能跑的。`pinned` 里的包例外 ——
    那是调用方（界面）明确点名的版本，选中它是用户的决定，不再用环境把它筛掉。

    `installed`（已装包 id -> 版本）是世界的现状：这些包和本次点名的根一起参与求解，
    先试「保住现状」那条路（已装版本排在候选最前），走不通时才移动它们。搬不动的已装包
    留在计划外 —— 它们本来就在磁盘上，没有要落的盘。
    """

    def __init__(
            self,
            registry: Registry,
            github: GitHubClient,
            environment: CapabilityEnvironment | None = None,
            pinned: frozenset[str] = frozenset(),
            installed: Mapping[str, str] | None = None,
            constrain_world: bool = True,
    ):
        self.registry = registry
        self.github = github
        self.environment = environment
        self.pinned = pinned
        # 目标游戏目录里已经装上的包：一个类型有多个供给者时，只能由已安装的那个决定目录。
        self.installed = dict(installed or {})
        # 「世界只作偏好与供给者选择」：执行某一行的决定时用它 —— 冲突在计划那一步已经处理完了，
        # 落盘不再因为世界不兼容而拒绝。
        self.constrain_world = constrain_world
        # 本次点名的根（`resolve_all` 填）：只有它们拿「点名的」那套候选顺序。
        self._named: frozenset[str] = frozenset()
        self._cache: dict[str, _Releases] = {}

    def _env_blocks(self, package: RegistryPackage, release: ReleaseInfo) -> bool:
        if self.environment is None or package.id in self.pinned:
            return False
        if self.installed.get(package.id) == str(release.version):
            # 已经装在这一版上：环境判定不该把它淘汰掉。否则机器上留着一个跑不起来的老包，
            # 之后连装别的模组都会失败 —— 现状再差也是现状。
            return False
        return (
            release_verdict(
                self.environment, category=package.category, dependencies=release.dependencies
            )
            == INCOMPATIBLE
        )

    def _releases(self, package_id: str) -> _Releases:
        """该包的可安装版本，另标出其中只因环境被淘汰的、与明确判为兼容的那些。"""
        cached = self._cache.get(package_id)
        if cached is not None:
            return cached
        package = self.registry.get(package_id)
        installable: list[ReleaseInfo] = []
        blocked: list[ReleaseInfo] = []
        compatible: list[ReleaseInfo] = []
        for release in self.github.releases(package):
            if not self.github.install_assets(package, release):
                continue
            installable.append(release)
            if self._env_blocks(package, release):
                blocked.append(release)
            if self._environment_verdict(package, release) == COMPATIBLE:
                compatible.append(release)
        if package_id in self._named and blocked and len(blocked) == len(installable):
            # 点名的根一个环境兼容的版本都没有：筛它没有意义，按最新装。用户点它就是要装，
            # 界面也才有机会在版本选择器里换个版本。
            blocked = []
        result = _Releases(tuple(installable), tuple(blocked), tuple(compatible))
        self._cache[package_id] = result
        return result

    def _environment_verdict(self, package: RegistryPackage, release: ReleaseInfo) -> str:
        """这条 release 在当前环境下的判定；没有环境可判时按「兼容」处理（不参与排序）。"""
        if self.environment is None:
            return COMPATIBLE
        return release_verdict(
            self.environment, category=package.category, dependencies=release.dependencies
        )

    def _candidates(
            self,
            package_id: str,
            constraints: Sequence[_Constraint],
            *,
            respect_environment: bool = True,
    ) -> tuple[ReleaseInfo, ...]:
        releases = self._releases(package_id)
        blocked_ids = (
            {release.id for release in releases.env_blocked} if respect_environment else set()
        )
        candidates = tuple(
            release
            for release in releases.installable
            if release.id not in blocked_ids
            and all(satisfies(release.version, item.range) for item in constraints)
        )
        if package_id in self._named:
            # 点名的根：环境明确说兼容的排前面（界面默认给的就是这个版本），其余的排在后面，
            # 免得世界里没有冲突时也去挑一个没有兼容声明的版本。
            compatible_ids = {release.id for release in releases.compatible}
            candidates = tuple(
                item for item in candidates if item.id in compatible_ids
            ) + tuple(item for item in candidates if item.id not in compatible_ids)
        installed_version = self.installed.get(package_id)
        if installed_version and package_id not in self._named:
            # 已装的包：已装的那一版排最前，回溯先走「不动它」这条路，只有走不通才换版本。
            candidates = tuple(
                item for item in candidates if str(item.version) == installed_version
            ) + tuple(item for item in candidates if str(item.version) != installed_version)
        return candidates

    def _game_version(self) -> str | None:
        """本机游戏版本；读不出来（没配路径、太老、文件坏）就没有版本可供筛选。"""
        environment = self.environment
        if environment is None:
            return None
        return environment.capability_version(environment.game_id)

    @staticmethod
    def _rows_version_range(rows: list[dict[str, Any]]) -> str:
        """同一个加载器命中的各行的版本约束：取并集。

        各行的 `version` 用 ` || ` 连接（去重、保持出现顺序）；某一行没写 `version`（或写成
        `*`）就是它不受版本约束，整体也就没有约束。`satisfies` 与 `range_allows` 都按 ` || `
        分段，并集可以直接当约束用。
        """
        ranges: list[str] = []
        for row in rows:
            value = str(row.get("version") or "").strip()
            if not value or value == "*":
                return "*"
            if value not in ranges:
                ranges.append(value)
        return " || ".join(ranges)

    def _provider_from_table(
            self,
            file_type: str,
            candidates: tuple[RegistryPackage, ...],
    ) -> tuple[RegistryPackage, str] | None:
        """按索引里的「加载器包 ↔ 游戏」表在候选里定一个供给者。

        表为空、或本机游戏版本未知时这层不知道答案，交还给调用方。留下的供给者恰有一个时返回
        它和它那几行的版本并集；一个都不留说明表里没有供给者认这台机器的游戏版本，直接报错；
        留下多个同样交还给调用方（表本身没定论）。
        """
        version = self._game_version()
        entries = self.registry.provider_table.get("entries") or ()
        if version is None or not entries:
            return None
        matches: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            rows = [
                entry
                for entry in entries
                if isinstance(entry, dict)
                and str(entry.get("loader", "")) == candidate.id
                and range_allows(version, entry.get("sprocket"))
            ]
            if rows:
                matches[candidate.id] = rows
        if not matches:
            ids = ", ".join(candidate.id for candidate in candidates)
            raise ResolutionError(
                f"no modloader provider targets this game version for install type "
                f"'{file_type}'; none of {ids} covers Sprocket {version}"
            )
        if len(matches) != 1:
            return None
        provider_id, rows = next(iter(matches.items()))
        provider = next(item for item in candidates if item.id == provider_id)
        return provider, self._rows_version_range(rows)

    def _loader_for_type(
            self,
            package: RegistryPackage,
            file_type: str,
            declared_ids: frozenset[str] = frozenset(),
    ) -> tuple[RegistryPackage, str] | None:
        """这个类型该跟着哪个加载器装，以及它该用哪段版本。

        优先顺序：只有唯一供给者时就是它。有多个供给者时，包自己声明的依赖点名了其中一个就用它
        （载荷是针对那份运行时构建的，声明的依赖比「加载器包 ↔ 游戏」表更准确）。包自己供给这个
        类型（加载器自己的载荷）时返回 None。剩下的多个候选由索引里的表来定：留下那几行里
        `sprocket` 区间含本机游戏版本的供给者，恰有一个就是它，版本跟着它那几行的 `version` 并集
        走 —— 表说这些窗口的加载器才配这台机器的游戏。表没给出唯一答案（留下多个、游戏版本未知、
        表为空）时才看已安装集合：只有恰好一个已安装才有答案，否则报错让用户先去加载器页挑一个。
        表里一个供给者都不认这台机器的游戏版本时也报错。计划里每个类型最多只出现一个供给者。
        """
        candidates = self.registry.providers_for_type(file_type)
        if not candidates:
            return None
        if package.id in {candidate.id for candidate in candidates}:
            # 包自己就供给这个类型（加载器自己的载荷），不需要把自己列成依赖。
            return None
        if len(candidates) == 1:
            return candidates[0], "*"
        for candidate in candidates:
            if candidate.id in declared_ids:
                return candidate, "*"
        selected = self._provider_from_table(file_type, candidates)
        if selected is not None:
            return selected
        installed = [candidate for candidate in candidates if candidate.id in self.installed]
        if len(installed) == 1:
            return installed[0], "*"
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

    def _companions_for(self, package: RegistryPackage) -> tuple[dict[str, str], ...]:
        """声明的伴随包。

        伴随边和依赖一样把对方拉进同一个计划、一样参与版本约束，但不进依赖图：一对互相需要的包
        本来就是一个环，安装顺序与环检测只看 `dependencies`（见 `resolve` 的排序）。
        没注册的伴随 id 不跟 —— Registry CI 会拦下它，客户端不该为一条坏数据崩掉整个解析。
        """
        return tuple(
            dict(companion)
            for companion in package.companions
            if self.registry.has_package(str(companion.get("id", "")))
        )

    def _dependencies_for(self, package: RegistryPackage, release: ReleaseInfo) -> tuple[dict[str, str], ...]:
        """声明的依赖 + 从静态安装类型推导出的加载器依赖。

        文件写着 `melonloader:mod` 就必须有供给这个类型的加载器在场上，所以它是一条真依赖：
        求解时一并装上，卸载时也按同一张依赖图判定能不能删。加载器自己供给的类型不算依赖。
        包声明的依赖已经点名了某个供给者时，那一条就是这条依赖，不再另加隐式依赖。隐式依赖的
        版本区间由选中的供给者给出（`*`，或表选中行的区间）。
        安装规则可以带 `when`（一个仓库两条发布线时按版本各装各的），所以类型要按**这条 release
        的版本**取：0.3.0 那条线依赖 MelonLoader，1.0.0 那条线依赖 BepInEx。

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
        for file_type in package.declared_types_for(str(release.version)):
            supplier = self._loader_for_type(package, file_type, frozenset(resolved_ids))
            if supplier is None:
                continue
            provider, version_range = supplier
            if provider.id in resolved_ids:
                continue
            resolved_ids.add(provider.id)
            implicit.append({"id": provider.id, "version": version_range, "when": "*"})
        return declared + tuple(implicit)

    @staticmethod
    def _explain(
            named: Mapping[str, str],
            dead_ends: list[_DeadEnd],
            constraints: dict[str, list[_Constraint]],
            environment: CapabilityEnvironment | None,
    ) -> str:
        """报出真正卡住的那条约束：最靠后的那次「无候选」+ 该包实际有哪些版本。

        卡住的包不是点名的根时，还要说清是**谁**要它、各自要哪个区间 —— 冲突的两边都得能看见，
        否则用户只知道「装不上」，不知道该动哪一个。

        整包都没有候选、且原因是环境（而不是版本约束）时说清这一点：用户得知道
        「这些版本都要求另一个 Sprocket / MelonLoader」，否则只会以为是依赖写错了。

        没有死路可报（理论上不该发生）时退回最初的约束表，至少给出请求的包。
        """
        if not dead_ends:
            return ", ".join(
                f"{package_id} {' & '.join(item.range for item in items)}"
                for package_id, items in constraints.items()
                if items
            )
        blocked = max(dead_ends, key=lambda item: (item.depth, len(item.constraints)))
        available = ", ".join(str(version) for version in blocked.available) or "none"
        if blocked.package_id in named:
            summary = (
                f"{blocked.package_id} {named[blocked.package_id]}; available releases: {available}"
            )
        else:
            requested = " & ".join(f"{package_id} {item}" for package_id, item in named.items())
            ranges = " & ".join(item.range for item in blocked.constraints)
            sources = ", ".join(dict.fromkeys(item.source for item in blocked.constraints))
            summary = (
                f"{requested} -> {blocked.package_id} {ranges} (required by {sources})"
                f"; available releases: {available}"
            )
        if blocked.constraint_only and environment is not None:
            # 约束本身是能满足的，是环境把这些版本全筛掉了 —— 这才是要报的原因。
            # （约束能满足但被环境筛掉时，上面那份 available 会把它们照常列出来，所以这里只看
            # 约束那一侧，不然这句话在有版本可列的时候就永远不会出现。）
            versions = ", ".join(str(version) for version in blocked.constraint_only)
            summary += (
                f"; {versions} requires another environment"
                f" (this machine: {environment.label()})"
            )
        return summary

    def resolve(self, root_id: str, root_range: str = "*") -> ResolutionPlan:
        return self.resolve_all({root_id: root_range})

    def resolve_all(
            self,
            roots: Mapping[str, str],
            *,
            ignore_edges: Iterable[tuple[str, str]] = (),
            excluded: Iterable[str] = (),
    ) -> ResolutionPlan:
        """一次解出这组根要装的版本，同时保住世界的现状。

        `roots` 是本次点名要装的包与各自的版本区间：同批一起解，它们之间也互相约束。
        已经装在目标目录里的包（`installed`）一起参与，所以「装 A 会把已装的 B 的依赖降级掉」
        这种解不会出现：要么挑出 A 的一个能与 B 共存的版本（点名的包优先让路），要么移动 B
        （只有这一条路时才动），要么报错说清是谁跟谁冲突。

        `ignore_edges` 里的「提要求的包 -> 依赖」不参与求解：那一版要装，但它这条依赖需求让位
        （宽容求解用它实现「兼容方赢、不兼容方照装」）。`excluded` 里的包谁都不许带进来 ——
        用户把它标成了「不安装」，它连当依赖都不算。
        """
        named = {
            self.registry.resolve_identifier(package_id).id: root_range
            for package_id, root_range in roots.items()
        }
        self._named = frozenset(named)
        ignored = {(str(source), str(dependency)) for source, dependency in ignore_edges}
        banned = {str(item) for item in excluded}
        constraints: dict[str, list[_Constraint]] = defaultdict(list)
        for package_id, root_range in named.items():
            constraints[package_id].append(_Constraint(root_range, package_id))
        for package_id in self.installed if self.constrain_world else ():
            if package_id in constraints:
                continue
            if not self.registry.has_package(package_id) or not self._candidates(package_id, ()):
                # 注册表不认的（下架、或没连上的私有服务器）、以及一个可安装版本都没有的包：
                # 拿不到它的需求，不能让它把整次安装堵死。
                continue
            constraints.setdefault(package_id, [])
        assignments: dict[str, ReleaseInfo] = {}
        dependencies: dict[str, tuple[str, ...]] = {}
        dead_ends: list[_DeadEnd] = []

        def search(
                current_constraints: dict[str, list[_Constraint]],
                current_assignments: dict[str, ReleaseInfo],
                current_dependencies: dict[str, tuple[str, ...]],
        ) -> tuple[dict[str, ReleaseInfo], dict[str, tuple[str, ...]]] | None:
            unresolved = [package_id for package_id in current_constraints if package_id not in current_assignments]
            if not unresolved:
                return current_assignments, current_dependencies

            candidate_sets = {
                package_id: (
                    self._candidates(package_id, current_constraints[package_id])
                    if self.registry.has_package(package_id)
                    # 注册表里没有这个 id：它一个候选都没有 —— 这是一条走不通的死路，不是崩溃。
                    else ()
                )
                for package_id in unresolved
            }
            blocked = False
            for package_id, candidates in candidate_sets.items():
                if candidates:
                    continue
                blocked = True
                unconstrained = (
                    self._candidates(package_id, (), respect_environment=False)
                    if self.registry.has_package(package_id)
                    else ()
                )
                dead_ends.append(
                    _DeadEnd(
                        depth=len(current_assignments),
                        package_id=package_id,
                        constraints=tuple(current_constraints[package_id]),
                        available=tuple(item.version for item in unconstrained),
                        constraint_only=tuple(
                            item.version
                            for item in (
                                self._candidates(
                                    package_id,
                                    current_constraints[package_id],
                                    respect_environment=False,
                                )
                                if self.registry.has_package(package_id)
                                else ()
                            )
                        ),
                    )
                )
            if blocked:
                return None

            # 已装的包先定：先试着把它们留在现在这一版上，点名的根再围着它们挑版本。
            package_id = min(
                unresolved,
                key=lambda item: (0 if item in self.installed else 1, len(candidate_sets[item])),
            )
            for release in candidate_sets[package_id]:
                package = self.registry.get(package_id)
                next_assignments = dict(current_assignments)
                next_assignments[package_id] = release
                next_constraints = {key: list(value) for key, value in current_constraints.items()}
                active_dependencies = tuple(
                    item
                    for item in self._dependencies_for(package, release)
                    if (package_id, str(item["id"])) not in ignored
                    and str(item["id"]) not in banned
                )
                active_companions = tuple(
                    item for item in self._companions_for(package) if str(item["id"]) not in banned
                )
                next_dependencies = dict(current_dependencies)
                next_dependencies[package_id] = tuple(item["id"] for item in active_dependencies)
                valid = True
                for dependency in active_dependencies + active_companions:
                    dependency_id = dependency["id"]
                    next_constraints.setdefault(dependency_id, []).append(
                        _Constraint(dependency["version"], package_id)
                    )
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
            details = self._explain(named, dead_ends, constraints, self.environment)
            raise ResolutionError(
                f"no compatible release set found ({details})", conflicts=self._conflicts(dead_ends)
            )
        assignments, dependencies = result
        companion_ids = self._companion_closure(assignments)
        installing = self._installing(assignments, named, dependencies, companion_ids)

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

        # 只跟 `dependencies`（含安装类型推出的隐含依赖）排序：伴随边不参与，环也只会从它那里过。
        # 计划里每个成员都要排到 —— 只靠伴随边进来的包从根上走不到，所以遍历计划本身而不是从根
        # 出发。`assignments` 按发现顺序插入，根在最前，同层之间因此是稳定的。
        for package_id in assignments:
            if package_id in installing:
                visit(package_id)
        packages = tuple(
            ResolvedPackage(
                package=self.registry.get(package_id),
                release=assignments[package_id],
                dependency_ids=dependencies.get(package_id, ()),
                companion_ids=companion_ids.get(package_id, ()),
            )
            for package_id in ordered_ids
        )
        return ResolutionPlan(next(iter(named)), packages, roots=tuple(named))

    @staticmethod
    def _conflicts(dead_ends: list[_DeadEnd]) -> tuple[dict, ...]:
        """卡得最深的那条死路：哪个包被卡住、各自是谁在要它。

        上层靠它说清冲突双方，并据此决定让谁出局、让谁的需求让位。
        """
        if not dead_ends:
            return ()
        blocked = max(dead_ends, key=lambda item: (item.depth, len(item.constraints)))
        # 原样给出所有约束：谁在要它、要的是什么区间。哪一条算「冲突方」（根自己的 `*` 种子不算、
        # 提出 `*` 的不算）由调用方过滤 —— 宽容求解还要靠这份原始的边去决定让哪一条。
        return ({
            "package_id": blocked.package_id,
            "requirements": tuple(
                {"source": item.source, "range": item.range} for item in blocked.constraints
            ),
        },)

    def _would_be_verdict(self, package_id: str, roots: Mapping[str, str]) -> str:
        """这个包会落在哪种环境判定上：点名了版本就用那一版，否则用它最该装的那一版。"""
        pinned = str(roots.get(package_id, ""))
        candidates = self._releases(package_id).installable
        if pinned.startswith("="):
            wanted = pinned[1:]
            candidates = tuple(item for item in candidates if str(item.version) == wanted)
        else:
            candidates = self._candidates(package_id, ()) or candidates
        if not candidates:
            return UNKNOWN
        return self._environment_verdict(self.registry.get(package_id), candidates[0])

    def resolve_tolerant(
            self,
            roots: Mapping[str, str],
            *,
            excluded: Iterable[str] = (),
    ) -> TolerantResolution:
        """解一批根；有包不能共存时按规则让路，不报错。

        冲突的双方**同色**（都判为兼容、或都不兼容）时两边都不选边，一起出局；**一兼容一不兼容**
        时兼容那方的要求赢，不兼容那方这条依赖要求让位 —— 它照装，缺口由调用方标出来。
        用户点名的那一版被卡住时算不出有效解（`plan` 为空），调用方保留上一个有效解。
        `excluded` 里的包连当依赖都不算（用户标了「不安装」）。
        """
        named = {
            self.registry.resolve_identifier(package_id).id: root_range
            for package_id, root_range in roots.items()
        }
        banned = frozenset(str(item) for item in excluded)
        first = next(iter(named), "")
        remaining = dict(named)
        dropped: dict[str, Dropped] = {}
        ignored: set[tuple[str, str]] = set()
        unmet: list[tuple[str, str]] = []
        while True:
            if not remaining:
                # 冲突把点名的包全出局了：这就是「空安装列表」，不是一个错误。
                return TolerantResolution(
                    ResolutionPlan(first, (), roots=()), tuple(dropped.values()), tuple(unmet)
                )
            try:
                plan = self.resolve_all(remaining, ignore_edges=ignored, excluded=banned)
            except ResolutionError as exc:
                conflict = exc.conflicts[0] if exc.conflicts else None
                if conflict is None:
                    return TolerantResolution(None, tuple(dropped.values()), tuple(unmet))
                blocked = str(conflict["package_id"])
                requirements = tuple(conflict["requirements"])
                # 冲突方是「提出了会排除这个包的区间」的那些：根自己的 `*` 种子不是冲突方，
                # 否则一个包被卡住时会把自己也算进去。
                sources = tuple(
                    dict.fromkeys(
                        str(item["source"])
                        for item in requirements
                        if str(item["source"]) != blocked
                        and str(item["range"]).strip() not in ("", "*")
                    )
                )
                before = (len(ignored), len(remaining))
                if not sources:
                    # 没有别人跟它冲突：这个包自己一个可用版本都没有。
                    if blocked in remaining:
                        if str(remaining[blocked]).startswith("="):
                            return TolerantResolution(None, tuple(dropped.values()), tuple(unmet))
                        remaining.pop(blocked)
                        dropped.setdefault(blocked, Dropped(blocked, "no-candidate", ()))
                    else:
                        # 依赖一个可用版本都没有：这一版照装，这条依赖让位（缺口由调用方标出）。
                        for item in requirements:
                            ignored.add((str(item["source"]), blocked))
                else:
                    verdicts = {
                        source: self._would_be_verdict(source, remaining) for source in sources
                    }
                    if COMPATIBLE in verdicts.values() and INCOMPATIBLE in verdicts.values():
                        # 一兼容一不兼容：兼容那方的要求赢，另一方这条要求让位（它照装）。
                        for source in sources:
                            if verdicts[source] == INCOMPATIBLE and (source, blocked) not in ignored:
                                ignored.add((source, blocked))
                                unmet.append((source, blocked))
                    elif any(str(remaining.get(source, "")).startswith("=") for source in sources):
                        # 同色冲突里掺着用户点名的那一版：两边都不选边就会把点名的也出局，
                        # 所以这一批算不出有效解，交给调用方保留上一个有效解。
                        return TolerantResolution(None, tuple(dropped.values()), tuple(unmet))
                    else:
                        # 只有一方在要它、而它还是解不出来：那不是「两边冲突」，是这一条依赖根本没得装。
                        reason = "conflict" if len(sources) > 1 else "missing-dependency"
                        others = sources if reason == "conflict" else (blocked,)
                        for source in sources:
                            remaining.pop(source, None)
                            dropped.setdefault(source, Dropped(source, reason, tuple(others)))
                if (len(ignored), len(remaining)) == before:
                    # 这条冲突已经没有能让的东西了：再转一圈也不会变。
                    return TolerantResolution(None, tuple(dropped.values()), tuple(unmet))
            else:
                return TolerantResolution(plan, tuple(dropped.values()), tuple(unmet))

    def _installing(
            self,
            assignments: dict[str, ReleaseInfo],
            named: Mapping[str, str],
            dependencies: dict[str, tuple[str, ...]],
            companions: dict[str, tuple[str, ...]],
    ) -> set[str]:
        """这次真的会落到盘上的那些包。

        点名的根 + 被移动的已装包，再加上它们的依赖与伴随闭包。保持原样的已装包只是求解的
        上下文：它们已经在磁盘上了，没有要落的盘 —— 计划里有它们只会让「要装什么」失焦。
        """
        installing = set(named)
        for package_id, release in assignments.items():
            installed_version = self.installed.get(package_id)
            if package_id in installing or not installed_version:
                continue
            if installed_version != str(release.version):
                installing.add(package_id)
        pending = list(installing)
        while pending:
            package_id = pending.pop()
            for related in (*dependencies.get(package_id, ()), *companions.get(package_id, ())):
                if related in assignments and related not in installing:
                    installing.add(related)
                    pending.append(related)
        return installing

    def _companion_closure(
            self,
            assignments: dict[str, ReleaseInfo],
    ) -> dict[str, tuple[str, ...]]:
        """计划里每个包的伴随包：自己点名的 + 点名了自己的，都限定在计划内。

        条目只要一边点名就够（另一边通常写成普通依赖），双向闭包让「一起卸」不取决于从哪里发起。
        指向计划外的伴随包不会出现 —— 求解时它们已经被拉进来了。
        """
        declared = {
            package_id: {
                str(companion.get("id")) for companion in self.registry.get(package_id).companions
            }
            for package_id in assignments
        }
        members = set(assignments)
        return {
            package_id: tuple(
                sorted(
                    (declared[package_id] & members)
                    | {
                        other
                        for other, ids in declared.items()
                        if package_id in ids
                    }
                )
            )
            for package_id in assignments
        }
