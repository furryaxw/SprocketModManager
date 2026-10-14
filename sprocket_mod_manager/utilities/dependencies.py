from __future__ import annotations

from ..domain.models import RegistryPackage, ReleaseInfo
from ..domain.semver import satisfies


def dependencies_for_release(
        package: RegistryPackage,
        release: ReleaseInfo,
) -> tuple[dict[str, str], ...]:
    """这一版在盘上需要的包：声明的依赖，加上整合包声明的成员。

    两者形状与 `when` 语义完全一样（`when` 是按**包自己这一版**筛选的区间），所以合成一份返回：
    求解、计划页的缺口说明、安装记录里的 `dependencies`、卸载级联都只看这一处。
    """
    return tuple(
        requirement
        for requirement in package.requirements()
        if satisfies(release.version, requirement.get("when", "*"))
    )
