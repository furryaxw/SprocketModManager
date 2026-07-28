from __future__ import annotations

from collections import defaultdict

from .errors import ResolutionError
from .github import GitHubClient
from .models import RegistryPackage, ReleaseInfo, ResolvedPackage, ResolutionPlan
from .registry import Registry
from .semver import satisfies


class DependencySolver:
    def __init__(self, registry: Registry, github: GitHubClient):
        self.registry = registry
        self.github = github

    def _candidates(self, package_id: str, constraints: list[str]) -> tuple[ReleaseInfo, ...]:
        package = self.registry.get(package_id)
        candidates = []
        for release in self.github.releases(package):
            if not self.github.install_assets(package, release):
                continue
            if all(satisfies(release.version, constraint) for constraint in constraints):
                candidates.append(release)
        return tuple(candidates)

    @staticmethod
    def _dependencies_for(package: RegistryPackage, release: ReleaseInfo) -> tuple[dict[str, str], ...]:
        return tuple(
            dependency
            for dependency in package.dependencies
            if satisfies(release.version, dependency.get("when", "*"))
        )

    def resolve(self, root_id: str, root_range: str = "*") -> ResolutionPlan:
        root = self.registry.resolve_identifier(root_id)
        constraints: dict[str, list[str]] = defaultdict(list)
        constraints[root.id].append(root_range)
        assignments: dict[str, ReleaseInfo] = {}
        dependencies: dict[str, tuple[str, ...]] = {}

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
            details = ", ".join(
                f"{package_id} {' & '.join(ranges)}" for package_id, ranges in constraints.items()
            )
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
