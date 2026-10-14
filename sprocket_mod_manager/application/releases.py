"""每个可安装版本的三色判定：目录读数、安装计划、更新判定共用这一份。

判定本身只有一处口径（`registry_core.compatibility`）：这里只负责把「这个包有哪些版本、
每一版判成什么」摆成界面与计划都能用的形状。
"""

from __future__ import annotations

from typing import Any

from ..domain.compatibility import CapabilityEnvironment, loader_table_decision, release_verdict
from ..domain.errors import ModManagerError
from ..domain.models import RegistryPackage, ReleaseInfo
from ..domain.registry import Registry
from ..infrastructure.github import GitHubClient
from ..utilities.dependencies import dependencies_for_release


def installable_release(
        package: RegistryPackage,
        release: ReleaseInfo,
        github: GitHubClient,
) -> bool:
    """这一版有没有可装的东西。

    普通包看有没有可安装资产；整合包还认清单带来的成员与落点规则 —— 纯成员包的 release 里可以
    只有清单、没有载荷，它照样能装（成员各自有自己的条目与载荷）。
    """
    if github.install_assets(package, release):
        return True
    if not package.is_pack:
        return False
    version = str(release.version)
    return bool(
        dependencies_for_release(package, release)
        or package.file_rules_for(version)
        or package.payload_rules_for(version)
    )


def missing_manifest_data(package: RegistryPackage, github: GitHubClient) -> bool:
    """整合包在索引里既没有成员也没有落点规则：它的清单还没被索引构建读进来。

    索引构建那一版读不到清单（下载失败，或者索引是本地/预览构建）时就是这个样子。说清原因，比
    让它显示成「没有可安装版本」有用。
    """
    if not package.is_pack:
        return False
    if package.members or package.file_rules or package.payload_rules:
        return False
    try:
        return bool(github.releases(package))
    except (ModManagerError, OSError, ValueError):
        return False


def release_verdicts(
        package: RegistryPackage,
        *,
        registry: Registry | None,
        github: GitHubClient,
        environment: CapabilityEnvironment,
) -> list[dict[str, Any]]:
    """该包每个可安装版本的三色判定（新到旧）。

    加载器按「加载器包 ↔ 游戏」表判：哪一行覆盖本机游戏版本，那一行的版本区间就是它该处的范围。
    加载器自己不写能力声明，按声明判只会得到「未知」。
    """
    if package.releases is not None:
        releases = package.releases
    else:
        try:
            releases = github.releases(package)
        except (ModManagerError, OSError, ValueError):
            return []
    table = registry.provider_table if registry is not None else {}
    game_capability = environment.game_id
    game_version = environment.capability_version(game_capability)
    verdicts: list[dict[str, Any]] = []
    for release in releases:
        if not installable_release(package, release, github):
            continue
        decision = (
            loader_table_decision(
                table, package.id, game_capability, game_version, str(release.version)
            )
            if package.is_loader
            else None
        )
        if decision is None:
            dependencies = release.dependencies
            verdict = release_verdict(
                environment, category=package.category, dependencies=dependencies
            )
        else:
            verdict, dependencies = decision
        verdicts.append({
            "tag": release.tag,
            "version": str(release.version),
            "verdict": verdict,
            "compatibility": dict(release.compatibility) if release.compatibility else None,
            # 声明的原始区间与逐轴结果：详情页照着摆，不再自己解析一遍。
            "dependencies": [dict(item) for item in dependencies],
            "axes": environment.axes(dependencies),
        })
    return verdicts
