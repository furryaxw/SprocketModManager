from __future__ import annotations

from ..domain.models import RegistryPackage, ReleaseInfo
from ..domain.semver import satisfies


def dependencies_for_release(
        package: RegistryPackage,
        release: ReleaseInfo,
) -> tuple[dict[str, str], ...]:
    return tuple(
        dependency
        for dependency in package.dependencies
        if satisfies(release.version, dependency.get("when", "*"))
    )
