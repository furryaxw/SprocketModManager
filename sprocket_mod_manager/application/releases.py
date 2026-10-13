"""每个可安装版本的三色判定：目录读数、安装计划、更新判定共用这一份。

判定本身只有一处口径（`registry_core.compatibility`）：这里只负责把「这个包有哪些版本、
每一版判成什么」摆成界面与计划都能用的形状。
"""

from __future__ import annotations

from typing import Any

from ..domain.compatibility import CapabilityEnvironment, loader_table_decision, release_verdict
from ..domain.errors import ModManagerError
from ..domain.models import RegistryPackage
from ..domain.registry import Registry
from ..infrastructure.github import GitHubClient


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
        if not github.install_assets(package, release):
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
