from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import RegistryError
from .models import RegistryPackage


class Registry:
    def __init__(self, packages: list[RegistryPackage]):
        self.packages = tuple(packages)
        self._by_id = {package.id: package for package in packages}
        if len(self._by_id) != len(packages):
            raise RegistryError("registry contains duplicate package ids")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Registry":
        if data.get("schema_version") != 1:
            raise RegistryError("unsupported registry schema_version")
        raw_packages = data.get("packages")
        if not isinstance(raw_packages, list):
            raise RegistryError("registry packages must be a list")
        packages: list[RegistryPackage] = []
        for raw in raw_packages:
            if not isinstance(raw, dict):
                raise RegistryError("registry package must be an object")
            try:
                packages.append(RegistryPackage.from_dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                raise RegistryError(f"invalid registry package: {exc}") from exc
        registry = cls(packages)
        for package in registry.packages:
            for dependency in package.dependencies:
                if dependency.get("id") not in registry._by_id:
                    raise RegistryError(
                        f"{package.id}: dependency is not registered: {dependency.get('id')}"
                    )
            for recommendation in package.recommendations:
                if recommendation == package.id:
                    raise RegistryError(f"{package.id}: package cannot recommend itself")
                if recommendation not in registry._by_id:
                    raise RegistryError(
                        f"{package.id}: recommendation is not registered: {recommendation}"
                    )
        return registry

    @classmethod
    def from_file(cls, path: Path) -> "Registry":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"cannot read registry {path}: {exc}") from exc
        return cls.from_dict(data)

    def get(self, package_id: str) -> RegistryPackage:
        try:
            return self._by_id[package_id]
        except KeyError as exc:
            raise RegistryError(f"package is not registered: {package_id}") from exc

    def resolve_identifier(self, value: str) -> RegistryPackage:
        direct = self._by_id.get(value)
        if direct:
            return direct
        folded = value.casefold()
        matches = [
            package
            for package in self.packages
            if package.name.casefold() == folded or package.id.casefold() == folded
        ]
        if len(matches) == 1:
            return matches[0]
        raise RegistryError(f"package is not registered: {value}")
