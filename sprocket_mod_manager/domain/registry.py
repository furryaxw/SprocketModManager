from __future__ import annotations

import json
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable

from .errors import RegistryError
from .models import RegistryPackage
from .compatibility import DEFAULT_GAME_CAPABILITY, providers_table
from ..utilities.package_paths import (
    file_type_is_wildcard,
    file_type_namespace,
    validate_file_type,
    validate_subpath,
    validate_supply_target,
    validate_supply_type,
)


class Registry:
    def __init__(
            self,
            packages: list[RegistryPackage],
            provider_table: dict[str, Any] | None = None,
            game_id: str = DEFAULT_GAME_CAPABILITY,
            game_name: str = "",
    ):
        self.packages = tuple(packages)
        self.game_id = str(game_id)
        self.game_name = str(game_name)
        self.provider_table = providers_table(provider_table)
        self._by_id = {package.id: package for package in packages}
        if len(self._by_id) != len(packages):
            raise RegistryError("registry contains duplicate package ids")
        self._suppliers = self._build_suppliers(packages)
        self._capabilities = self._build_capabilities(packages)
        self._validate_sources(packages)

    @staticmethod
    def _validate_sources(packages: tuple[RegistryPackage, ...] | list[RegistryPackage]) -> None:
        """非 GitHub 来源必须自带 release 数据，并写明允许下载的主机。

        外部来源没有可以查询的 API，所以版本与资产只能由注册表条目自己给出；主机白名单
        保证下载地址不会漂到任意站点。只有基础运行时可以声明外部来源：安装类型目录由加载器的
        供给表定义，模组的二进制始终来自它自己的 GitHub Releases。
        """
        for package in packages:
            source = package.source
            source_type = str(source.get("type", "github"))
            if package.is_modloader and not package.supply:
                raise RegistryError(
                    f"{package.id}: a modloader must supply at least one install type"
                )
            if source_type == "github":
                if "hosts" in source:
                    raise RegistryError(
                        f"{package.id}: release.source.hosts only applies to external sources"
                    )
                continue
            if source_type != "external":
                raise RegistryError(f"{package.id}: unknown release source type: {source_type}")
            if not package.is_modloader:
                raise RegistryError(
                    f"{package.id}: only a modloader may declare an external release source"
                )
            if not package.asset_hosts():
                raise RegistryError(
                    f"{package.id}: external release source needs at least one allowed host"
                )
            if not package.releases:
                raise RegistryError(
                    f"{package.id}: external release source needs an embedded releases list"
                )

    @staticmethod
    def _build_capabilities(packages: Iterable[RegistryPackage]) -> frozenset[str]:
        """索引里认得的能力 id：各包显式提供的能力，外加没写 `provides` 的加载器类包自己。

        模组自己的包 id 是**包**，不是能力轴 —— 模组之间靠包依赖相连。加载器类包没写
        `provides` 时隐含提供自己的包 id，能力轴与包 id 同名。
        """
        capabilities: set[str] = set()
        for package in packages:
            declared = package.declared_capabilities()
            if declared:
                capabilities.update(declared)
            elif package.is_loader:
                capabilities.add(package.id)
        return frozenset(capabilities)

    @staticmethod
    def _build_suppliers(
            packages: tuple[RegistryPackage, ...] | list[RegistryPackage],
    ) -> dict[str, tuple[RegistryPackage, ...]]:
        """类型 -> 供给它的加载器（可能不止一个）。

        一个类型可以有多个供给者：同一套模组既可能跑在原生加载器下，也可能跑在把目录重新
        安家的桥接加载器下，安装位置取决于实际装的是哪一个。同时校验每个包声明的类型、供给
        位置，以及静态规则里的类型确实有人供给：模组能不能装，在注册表这一步就要能回答。
        """
        suppliers: dict[str, list[RegistryPackage]] = {}

        def add(key: str, package: RegistryPackage) -> None:
            providers = suppliers.setdefault(key, [])
            if package not in providers:
                providers.append(package)

        for package in packages:
            for raw_type, raw_target in package.supply.items():
                file_type = validate_supply_type(raw_type)
                validate_supply_target(raw_target)
                add(file_type, package)
            for rule in package.file_rules:
                validate_file_type(str(rule.get("type", "")))
                if rule.get("subpath"):
                    validate_subpath(str(rule["subpath"]))
            for rule in package.payload_rules:
                validate_supply_target(str(rule.get("target", "")))
                if rule.get("subpath"):
                    validate_subpath(str(rule["subpath"]))
        for package in packages:
            for file_type in package.declared_types():
                if not any(
                    file_type == supplied or (
                        file_type_is_wildcard(file_type)
                        and file_type_namespace(supplied) == file_type_namespace(file_type)
                    )
                    for supplied in suppliers
                ):
                    raise RegistryError(
                        f"{package.id}: install file type is not supplied by any modloader: {file_type}"
                    )
        return {key: tuple(value) for key, value in suppliers.items()}

    def providers_for_type(self, file_type: str) -> tuple[RegistryPackage, ...]:
        """供给这个类型的所有加载器；`<ns>:*` 解析成该名字空间下所有类型的供给者。"""
        validated = validate_file_type(file_type)
        if file_type_is_wildcard(validated):
            namespace = file_type_namespace(validated)
            providers: dict[str, RegistryPackage] = {}
            for supplied, candidates in self._suppliers.items():
                if file_type_namespace(supplied) != namespace:
                    continue
                for candidate in candidates:
                    providers.setdefault(candidate.id, candidate)
            return tuple(providers.values())
        return self._suppliers.get(validated, ())

    def supplier_for_type(
            self,
            file_type: str,
            installed: Iterable[str] = (),
    ) -> RegistryPackage | None:
        """确定这个类型该由哪个加载器供给；无法唯一确定时返回 None。

        只有一个供给者就是它。有多个供给者时只看已安装的那一个：装上的加载器决定了目录，
        不能靠猜。一个都没装（或装了不止一个）就没有唯一答案，由调用方解释给用户。
        """
        candidates = self.providers_for_type(file_type)
        if len(candidates) == 1:
            return candidates[0]
        installed_ids = set(installed)
        matches = [package for package in candidates if package.id in installed_ids]
        if len(matches) == 1:
            return matches[0]
        return None

    def install_directories(self, installed: Iterable[str] = ()) -> dict[str, PurePosixPath]:
        """类型 -> 游戏根目录下的相对目录（`{Sprocket}` 前缀已去掉）。

        只列出能唯一确定供给者的类型；有多个候选且无法从已安装集合里定下来的类型不在这里，
        调用方必须自己先报错，而不是拿一个猜出来的目录去安装。
        """
        directories: dict[str, PurePosixPath] = {}
        for file_type in self._suppliers:
            provider = self.supplier_for_type(file_type, installed)
            if provider is None:
                continue
            directories[file_type] = validate_supply_target(provider.supply[file_type])
        return directories

    def modloaders(self) -> tuple[RegistryPackage, ...]:
        return tuple(package for package in self.packages if package.is_modloader)

    def knows_capability(self, capability_id: str) -> bool:
        return capability_id == self.game_id or capability_id in self._capabilities

    def has_package(self, package_id: str) -> bool:
        """这个 id 是不是一个真实包（能力不是包：装不了，也不进依赖图）。"""
        return package_id in self._by_id

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Registry":
        if data.get("schema_version") != 1:
            raise RegistryError("unsupported registry schema_version")
        raw_packages = data.get("packages")
        if not isinstance(raw_packages, list):
            raise RegistryError("registry packages must be a list")
        raw_game = data.get("game")
        game_id = DEFAULT_GAME_CAPABILITY
        game_name = ""
        if isinstance(raw_game, dict) and raw_game.get("id"):
            game_id = str(raw_game["id"])
            game_name = str(raw_game.get("name", ""))
        packages: list[RegistryPackage] = []
        for raw in raw_packages:
            if not isinstance(raw, dict):
                raise RegistryError("registry package must be an object")
            try:
                packages.append(RegistryPackage.from_dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                raise RegistryError(f"invalid registry package: {exc}") from exc
        registry = cls(packages, data.get("providers"), game_id, game_name)
        for package in registry.packages:
            for dependency in package.dependencies:
                dependency_id = dependency.get("id")
                if dependency_id not in registry._by_id and not registry.knows_capability(
                    str(dependency_id)
                ):
                    raise RegistryError(
                        f"{package.id}: dependency is not registered: {dependency_id}"
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
